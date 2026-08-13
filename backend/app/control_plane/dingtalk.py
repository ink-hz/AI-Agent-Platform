from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
import inspect
import logging
import re
import time
from typing import Any, TypeVar
from uuid import uuid4

import httpx


_LOG = logging.getLogger(__name__)
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_LOGIN_FLOWS = frozenset({"qr", "in_client"})
_INVALID_APPLICATION_TOKEN_CODES = frozenset({40014, 42001})
_LOG_SECRETS: ContextVar[tuple[str, ...]] = ContextVar(
    "dingtalk_log_secrets", default=()
)
_Parsed = TypeVar("_Parsed")


def _redact_log_value(value: object) -> str:
    rendered = str(value)
    rendered = re.sub(
        r"(?i)(access_token=)[^&\s'\"<>]+", r"\1<redacted>", rendered
    )
    for secret in _LOG_SECRETS.get():
        if secret:
            rendered = rendered.replace(secret, "<redacted>")
    return rendered


class _ProviderLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_log_value(record.msg)
        if isinstance(record.args, tuple):
            sanitized: list[object] = []
            for value in record.args:
                redacted = _redact_log_value(value)
                sanitized.append(redacted if redacted != str(value) else value)
            record.args = tuple(sanitized)
        elif isinstance(record.args, dict):
            record.args = {
                key: _redact_log_value(value) for key, value in record.args.items()
            }
        return True


_PROVIDER_LOG_FILTER = _ProviderLogFilter()


def _install_provider_log_filter() -> None:
    # httpx emits at ``httpx``; httpcore uses these concrete module loggers.
    for name in (
        "httpx",
        "httpcore",
        "httpcore.connection",
        "httpcore.connection_pool",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
    ):
        logger = logging.getLogger(name)
        if not any(item is _PROVIDER_LOG_FILTER for item in logger.filters):
            logger.addFilter(_PROVIDER_LOG_FILTER)


@dataclass(frozen=True)
class DingTalkMember:
    userid: str = field(repr=False)
    unionid: str = field(repr=False)
    display_name: str = field(repr=False)
    active: bool
    department_ids: tuple[int, ...]


@dataclass(frozen=True)
class DingTalkAuthResult:
    unionid: str = field(repr=False)
    userid: str | None = field(repr=False)
    corp_id: str = field(repr=False)


@dataclass(frozen=True)
class DingTalkDepartment:
    department_id: int
    parent_department_id: int | None
    display_name: str


@dataclass(frozen=True)
class _ProviderResponse:
    payload: dict[str, Any]
    request_id: str
    attempts: int


class DingTalkProviderError(RuntimeError):
    """A stable provider failure containing no request or identity material."""

    def __init__(self, message: str, *, request_id: str, error_code: str) -> None:
        self.request_id = request_id
        self.error_code = _safe_error_code(error_code)
        super().__init__(
            f"{message}; request_id={request_id}; code={self.error_code}"
        )


def _safe_error_code(value: object) -> str:
    rendered = str(value) if isinstance(value, (str, int)) else "provider_error"
    return rendered if _SAFE_ERROR_CODE.fullmatch(rendered) else "provider_error"


def _required_object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError
    return value


def _required_string(value: object, *, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or "\0" in value
    ):
        raise ValueError
    return value


def _required_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError
    return value


def _required_legacy_error_code(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if (
        isinstance(value, str)
        and value
        and value == value.strip()
        and re.fullmatch(r"-?[0-9]+", value)
    ):
        return int(value)
    raise ValueError


def _required_boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError
    return value


def _optional_integer(value: object) -> int | None:
    return None if value is None else _required_integer(value)


def _parse_provider_value(
    parser: Callable[[], _Parsed],
) -> _Parsed | None:
    """Discard raw parser exceptions before callers construct safe errors."""
    try:
        return parser()
    except (TypeError, ValueError):
        return None


class DingTalkClient:
    """Minimal, redacting DingTalk OpenAPI boundary for one fixed login flow.

    A self-created client ignores proxy environment variables. An injected client is
    an explicit caller trust boundary: its proxy, transport and lifecycle remain the
    caller's responsibility, while this boundary still redacts provider log records.
    """

    __slots__ = (
        "_api_base_url",
        "_app_key",
        "_app_secret",
        "_client",
        "_corp_id",
        "_login_flow",
        "_now",
        "_oapi_base_url",
        "_owns_client",
        "_sleep",
        "_token",
        "_token_expires_at",
        "_token_lock",
        "_timeout",
        "_initialized",
    )

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        corp_id: str,
        login_flow: str,
        api_base_url: str = "https://api.dingtalk.com",
        oapi_base_url: str = "https://oapi.dingtalk.com",
        timeout: httpx.Timeout | None = None,
        http_client: httpx.AsyncClient | None = None,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None] | None] = asyncio.sleep,
    ) -> None:
        for value in (app_key, app_secret, corp_id):
            _required_string(value)
        if login_flow not in _LOGIN_FLOWS:
            raise ValueError("DingTalk login flow invalid")
        for value in (api_base_url, oapi_base_url):
            if not isinstance(value, str) or not value.startswith("https://"):
                raise ValueError("DingTalk base URL invalid")
        selected_timeout = timeout or httpx.Timeout(
            connect=2.0, read=5.0, write=5.0, pool=2.0
        )
        _install_provider_log_filter()
        object.__setattr__(self, "_app_key", app_key)
        object.__setattr__(self, "_app_secret", app_secret)
        object.__setattr__(self, "_corp_id", corp_id)
        object.__setattr__(self, "_login_flow", login_flow)
        object.__setattr__(self, "_api_base_url", api_base_url.rstrip("/"))
        object.__setattr__(self, "_oapi_base_url", oapi_base_url.rstrip("/"))
        object.__setattr__(self, "_timeout", selected_timeout)
        object.__setattr__(
            self,
            "_client",
            http_client
            or httpx.AsyncClient(timeout=selected_timeout, trust_env=False),
        )
        object.__setattr__(self, "_owns_client", http_client is None)
        object.__setattr__(self, "_now", now)
        object.__setattr__(self, "_sleep", sleep)
        object.__setattr__(self, "_token", None)
        object.__setattr__(self, "_token_expires_at", 0.0)
        object.__setattr__(self, "_token_lock", asyncio.Lock())
        object.__setattr__(self, "_initialized", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_initialized", False):
            raise AttributeError("DingTalkClient configuration is immutable")
        object.__setattr__(self, name, value)

    def __repr__(self) -> str:
        return (
            "DingTalkClient(app_key=<redacted>, app_secret=<redacted>, "
            f"corp_id=<redacted>, login_flow={self._login_flow!r})"
        )

    @property
    def timeout(self) -> httpx.Timeout:
        return self._timeout

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _sleep_for(self, seconds: float) -> None:
        result = self._sleep(seconds)
        if inspect.isawaitable(result):
            await result

    def _error(
        self,
        message: str,
        *,
        request_id: str,
        error_code: object = "provider_error",
    ) -> DingTalkProviderError:
        error = DingTalkProviderError(
            message,
            request_id=request_id,
            error_code=_safe_error_code(error_code),
        )
        _LOG.warning("DingTalk provider request failed: %s", error)
        return error

    async def _request(
        self,
        method: str,
        url: str,
        *,
        request_id: str,
        retry_read: bool,
        max_attempts: int | None = None,
        **kwargs: Any,
    ) -> _ProviderResponse:
        headers = dict(kwargs.pop("headers", {}))
        headers["X-Request-Id"] = request_id
        attempts = max_attempts or (3 if retry_read else 1)
        if attempts < 1 or attempts > 3:
            raise ValueError("provider attempt budget invalid")
        sensitive_values = tuple(
            str(value)
            for key, value in (
                list(dict(kwargs.get("params", {})).items())
                + list(headers.items())
                + list(dict(kwargs.get("json", {})).items())
            )
            if str(key).lower() in {
                "access_token",
                "x-acs-dingtalk-access-token",
                "appsecret",
                "clientsecret",
                "code",
            }
            and isinstance(value, str)
        )
        response: httpx.Response | None = None
        for attempt in range(attempts):
            secret_context = _LOG_SECRETS.set(sensitive_values)
            transport_error: DingTalkProviderError | None = None
            try:
                response = await self._client.request(
                    method,
                    url,
                    headers=headers,
                    timeout=self._timeout,
                    **kwargs,
                )
            except asyncio.CancelledError:
                raise
            except httpx.RequestError:
                transport_error = self._error(
                    "DingTalk provider unavailable",
                    request_id=request_id,
                    error_code="transport_error",
                )
            finally:
                _LOG_SECRETS.reset(secret_context)
            if transport_error is not None:
                raise transport_error
            retryable = response.status_code == 429 or response.status_code >= 500
            if retry_read and retryable and attempt + 1 < attempts:
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = min(max(float(retry_after), 0.0), 2.0)
                except (TypeError, ValueError):
                    delay = 0.1 * (2**attempt)
                await self._sleep_for(delay)
                continue
            break
        assert response is not None
        payload = _parse_provider_value(
            lambda: _required_object(response.json())
        )
        if payload is None:
            raise self._error(
                "DingTalk response invalid",
                request_id=request_id,
                error_code=f"http_{response.status_code}",
            )
        if response.status_code >= 400:
            raise self._error(
                "DingTalk provider unavailable",
                request_id=request_id,
                error_code=payload.get("code", payload.get("errcode", f"http_{response.status_code}")),
            )
        return _ProviderResponse(payload, request_id, attempt + 1)

    async def _application_token(self) -> str:
        if self._token is not None and self._now() < self._token_expires_at:
            return self._token
        async with self._token_lock:
            if self._token is not None and self._now() < self._token_expires_at:
                return self._token
            request_id = str(uuid4())
            response = await self._request(
                "POST",
                f"{self._api_base_url}/v1.0/oauth2/accessToken",
                request_id=request_id,
                retry_read=False,
                json={"appKey": self._app_key, "appSecret": self._app_secret},
            )
            def parse_token() -> tuple[str, int]:
                token = _required_string(
                    response.payload.get("accessToken"), maximum=4096
                )
                expires_in = _required_integer(response.payload.get("expireIn"))
                if expires_in <= 0:
                    raise ValueError
                return token, expires_in

            parsed_token = _parse_provider_value(parse_token)
            if parsed_token is None:
                raise self._error(
                    "DingTalk response invalid",
                    request_id=response.request_id,
                    error_code="invalid_token_response",
                )
            token, expires_in = parsed_token
            skew = min(300, max(1, expires_in // 10))
            object.__setattr__(self, "_token", token)
            object.__setattr__(self, "_token_expires_at", self._now() + expires_in - skew)
            return token

    async def _invalidate_application_token(self, rejected_token: str) -> None:
        async with self._token_lock:
            if self._token == rejected_token:
                object.__setattr__(self, "_token", None)
                object.__setattr__(self, "_token_expires_at", 0.0)

    async def _legacy_read(
        self, path: str, body: dict[str, Any]
    ) -> _ProviderResponse:
        request_id = str(uuid4())
        remaining_attempts = 3
        refreshed_token = False
        while remaining_attempts:
            token = await self._application_token()
            response = await self._request(
                "POST",
                f"{self._oapi_base_url}{path}",
                request_id=request_id,
                retry_read=True,
                max_attempts=remaining_attempts,
                params={"access_token": token},
                json=body,
            )
            remaining_attempts -= response.attempts
            errcode = _parse_provider_value(
                lambda: _required_legacy_error_code(
                    response.payload.get("errcode")
                )
            )
            if errcode is None:
                raise self._error(
                    "DingTalk response invalid",
                    request_id=response.request_id,
                    error_code="invalid_envelope",
                )
            if errcode == 0:
                return response
            if (
                errcode in _INVALID_APPLICATION_TOKEN_CODES
                and not refreshed_token
                and remaining_attempts > 0
            ):
                refreshed_token = True
                await self._invalidate_application_token(token)
                continue
            raise self._error(
                "DingTalk provider rejected request",
                request_id=response.request_id,
                error_code=errcode,
            )
        raise self._error(
            "DingTalk provider unavailable",
            request_id=request_id,
            error_code="attempt_budget_exhausted",
        )

    async def exchange_login_code(
        self, code: str, code_verifier: str | None = None
    ) -> DingTalkAuthResult:
        _required_string(code, maximum=2048)
        if self._login_flow == "in_client":
            token = await self._application_token()
            request_id = str(uuid4())
            response = await self._request(
                "POST",
                f"{self._oapi_base_url}/topapi/v2/user/getuserinfo",
                request_id=request_id,
                retry_read=False,
                params={"access_token": token},
                json={"code": code},
            )
            errcode = _parse_provider_value(
                lambda: _required_legacy_error_code(
                    response.payload.get("errcode")
                )
            )
            if errcode is None:
                raise self._error(
                    "DingTalk response invalid",
                    request_id=response.request_id,
                    error_code="invalid_login_response",
                )
            if errcode != 0:
                raise self._error(
                    "DingTalk login rejected",
                    request_id=response.request_id,
                    error_code=errcode,
                )

            def parse_login() -> DingTalkAuthResult:
                result = _required_object(response.payload.get("result"))
                return DingTalkAuthResult(
                    unionid=_required_string(result.get("unionid")),
                    userid=_required_string(result.get("userid")),
                    corp_id=self._corp_id,
                )

            login = _parse_provider_value(parse_login)
            if login is None:
                raise self._error(
                    "DingTalk response invalid",
                    request_id=response.request_id,
                    error_code="invalid_login_response",
                )
            return login

        request_id = str(uuid4())
        if code_verifier is not None:
            _required_string(code_verifier, maximum=128)
            if len(code_verifier) < 43:
                raise ValueError("PKCE verifier invalid")
        token_request = {
            "clientId": self._app_key,
            "clientSecret": self._app_secret,
            "code": code,
            "grantType": "authorization_code",
        }
        if code_verifier is not None:
            token_request["codeVerifier"] = code_verifier
        token_response = await self._request(
            "POST",
            f"{self._api_base_url}/v1.0/oauth2/userAccessToken",
            request_id=request_id,
            retry_read=False,
            json=token_request,
        )
        token_identity = _parse_provider_value(lambda: (
            _required_string(
                token_response.payload.get("accessToken"), maximum=4096
            ),
            _required_string(token_response.payload.get("corpId")),
        ))
        if token_identity is None:
            raise self._error(
                "DingTalk response invalid",
                request_id=token_response.request_id,
                error_code="invalid_login_response",
            )
        user_token, corp_id = token_identity
        if corp_id != self._corp_id:
            raise self._error(
                "DingTalk organization mismatch",
                request_id=token_response.request_id,
                error_code="corp_mismatch",
            )
        profile_request_id = str(uuid4())
        profile_response = await self._request(
            "GET",
            f"{self._api_base_url}/v1.0/contact/users/me",
            request_id=profile_request_id,
            retry_read=True,
            headers={"x-acs-dingtalk-access-token": user_token},
        )
        unionid = _parse_provider_value(
            lambda: _required_string(profile_response.payload.get("unionId"))
        )
        if unionid is None:
            raise self._error(
                "DingTalk response invalid",
                request_id=profile_response.request_id,
                error_code="invalid_profile_response",
            )
        return DingTalkAuthResult(unionid=unionid, userid=None, corp_id=corp_id)

    @staticmethod
    def _member(payload: object, *, request_id: str) -> DingTalkMember:
        def parse_member() -> DingTalkMember:
            result = _required_object(payload)
            raw_departments = result.get("dept_id_list")
            if not isinstance(raw_departments, list):
                raise ValueError
            departments = tuple(_required_integer(item) for item in raw_departments)
            return DingTalkMember(
                userid=_required_string(result.get("userid")),
                unionid=_required_string(result.get("unionid")),
                display_name=_required_string(result.get("name"), maximum=256),
                active=_required_boolean(result.get("active")),
                department_ids=departments,
            )

        member = _parse_provider_value(parse_member)
        if member is None:
            raise DingTalkProviderError(
                "DingTalk response invalid",
                request_id=request_id,
                error_code="invalid_member_response",
            )
        return member

    async def resolve_union_member(self, unionid: str) -> DingTalkMember:
        _required_string(unionid)
        response = await self._legacy_read(
            "/topapi/user/getbyunionid", {"unionid": unionid}
        )
        def parse_union() -> tuple[int, str]:
            result = _required_object(response.payload.get("result"))
            return (
                _required_integer(result.get("contact_type")),
                _required_string(result.get("userid")),
            )

        union_result = _parse_provider_value(parse_union)
        if union_result is None:
            raise DingTalkProviderError(
                "DingTalk response invalid",
                request_id=response.request_id,
                error_code="invalid_union_response",
            )
        contact_type, userid = union_result
        if contact_type != 0:
            raise DingTalkProviderError(
                "DingTalk member unavailable",
                request_id=response.request_id,
                error_code="not_internal_member",
            )
        member, member_request_id = await self._get_member(userid)
        if member.unionid != unionid:
            raise DingTalkProviderError(
                "DingTalk identity mismatch",
                request_id=member_request_id,
                error_code="identity_mismatch",
            )
        return member

    async def get_member(self, userid: str) -> DingTalkMember:
        member, _ = await self._get_member(userid)
        return member

    async def _get_member(self, userid: str) -> tuple[DingTalkMember, str]:
        _required_string(userid)
        response = await self._legacy_read(
            "/topapi/v2/user/get", {"userid": userid, "language": "zh_CN"}
        )
        member = self._member(
            response.payload.get("result"), request_id=response.request_id
        )
        if member.userid != userid:
            raise DingTalkProviderError(
                "DingTalk identity mismatch",
                request_id=response.request_id,
                error_code="identity_mismatch",
            )
        return member, response.request_id

    async def iter_departments(self) -> AsyncIterator[DingTalkDepartment]:
        pending = [1]
        seen = {1}
        while pending:
            if len(seen) > 10_000:
                raise DingTalkProviderError(
                    "DingTalk pagination invalid",
                    request_id=str(uuid4()),
                    error_code="department_bound",
                )
            parent_id = pending.pop(0)
            response = await self._legacy_read(
                "/topapi/v2/department/listsub",
                {"dept_id": parent_id, "language": "zh_CN"},
            )
            result = response.payload.get("result")
            if not isinstance(result, list):
                raise DingTalkProviderError(
                    "DingTalk response invalid",
                    request_id=response.request_id,
                    error_code="invalid_department_response",
                )
            for item in result:
                def parse_department() -> DingTalkDepartment:
                    source = _required_object(item)
                    return DingTalkDepartment(
                        department_id=_required_integer(source.get("dept_id")),
                        parent_department_id=_optional_integer(source.get("parent_id")),
                        display_name=_required_string(source.get("name"), maximum=256),
                    )

                department = _parse_provider_value(parse_department)
                if department is None:
                    raise DingTalkProviderError(
                        "DingTalk response invalid",
                        request_id=response.request_id,
                        error_code="invalid_department_response",
                    )
                if department.department_id in seen:
                    raise DingTalkProviderError(
                        "DingTalk department cycle",
                        request_id=response.request_id,
                        error_code="department_cycle",
                    )
                seen.add(department.department_id)
                pending.append(department.department_id)
                yield department

    async def iter_department_members(
        self, department_id: int
    ) -> AsyncIterator[DingTalkMember]:
        department_id = _required_integer(department_id)
        cursor = 0
        seen_cursors = {cursor}
        page_count = 0
        while True:
            page_count += 1
            if page_count > 10_000:
                raise DingTalkProviderError(
                    "DingTalk pagination invalid",
                    request_id=str(uuid4()),
                    error_code="pagination_bound",
                )
            response = await self._legacy_read(
                "/topapi/v2/user/list",
                {
                    "dept_id": department_id,
                    "cursor": cursor,
                    "size": 100,
                    "contain_access_limit": False,
                    "language": "zh_CN",
                },
            )
            def parse_page() -> tuple[list[object], bool, int]:
                result = _required_object(response.payload.get("result"))
                entries = result.get("list")
                if not isinstance(entries, list):
                    raise ValueError
                return (
                    entries,
                    _required_boolean(result.get("has_more")),
                    _required_integer(result.get("next_cursor")),
                )

            page = _parse_provider_value(parse_page)
            if page is None:
                raise DingTalkProviderError(
                    "DingTalk response invalid",
                    request_id=response.request_id,
                    error_code="invalid_member_page",
                )
            entries, has_more, next_cursor = page
            if has_more and not entries:
                raise DingTalkProviderError(
                    "DingTalk pagination invalid",
                    request_id=response.request_id,
                    error_code="pagination_empty_page",
                )
            for entry in entries:
                yield self._member(entry, request_id=response.request_id)
            if not has_more:
                return
            if next_cursor in seen_cursors:
                raise DingTalkProviderError(
                    "DingTalk pagination invalid",
                    request_id=response.request_id,
                    error_code="pagination_cycle",
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
