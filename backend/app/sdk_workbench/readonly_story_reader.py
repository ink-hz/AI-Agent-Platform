"""Dedicated, bounded GET transport for the caller-declared story snapshot scope."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from urllib.parse import urlsplit

import httpx

from .story_snapshot import SnapshotScope
from .zentao import (ZenTaoAuthenticationError, ZenTaoProtocolError, ZenTaoRejected,
                     ZenTaoRequestRejected, ZenTaoUnavailable)

_MAX_BYTES = 1_048_576
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")


def _origin(value: str) -> str:
    try:
        if (not isinstance(value, str) or not value or any(c.isspace() for c in value)
                or any(unicodedata.category(c) == "Cc" for c in value)
                or any(c in value for c in ("\\", "?", "#"))):
            raise ValueError
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (parsed.scheme not in {"http", "https"} or not host
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or parsed.netloc.endswith(":") or (port is not None and not 1 <= port <= 65535)):
            raise ValueError
        # urlsplit validates bracketed IPv6. Validate DNS/IDNA hosts separately.
        if ":" not in host:
            ascii_host = host.encode("idna").decode("ascii").rstrip(".")
            if (len(ascii_host) > 253 or not ascii_host
                    or any(not _HOST.fullmatch(label) for label in ascii_host.split("."))):
                raise ValueError
        return str(httpx.URL(value)).rstrip("/")
    except (ValueError, UnicodeError, httpx.InvalidURL):
        raise ValueError("readonly_origin_invalid") from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("nonstandard_json_constant")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite_json_number")
    return number


class ReadOnlyStoryReader:
    """Bind one actor object and scope to a caller-supplied service Token.

    No login, credential loading, authorization inference, refresh or retry occurs.
    Scope and actor metadata remain caller declarations, not verified permissions.
    """

    def __init__(self, origin: str, token: str, scope: SnapshotScope, actor: object,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        origin = _origin(origin)
        if (not isinstance(token, str) or not token
                or any(unicodedata.category(c) == "Cc" for c in token)):
            raise ValueError("readonly_token_invalid")
        if not isinstance(scope, SnapshotScope):
            raise ValueError("readonly_scope_invalid")
        if actor is None:
            raise ValueError("readonly_actor_required")
        try:
            token_bytes = token.encode("utf-8")
        except UnicodeError:
            raise ValueError("readonly_token_invalid") from None
        self._scope, self._actor = scope, actor
        self._token = token_bytes
        self._closed = False
        self._client = httpx.AsyncClient(base_url=origin, transport=transport,
            timeout=10.0, trust_env=False, follow_redirects=False)

    @property
    def scope(self) -> SnapshotScope:
        return self._scope

    async def close(self) -> None:
        await self._client.aclose()
        self._closed = True

    async def request(self, actor: object, method: str, path: str,
                      payload: dict | None = None, params: dict | None = None,
                      *, api_version: int = 2) -> dict:
        if (actor is not self._actor or method != "GET" or type(api_version) is not int
                or api_version != 2 or path != f"products/{self._scope.product_id}/stories"
                or payload is not None or not isinstance(params, dict)
                or set(params) != {"browseType", "recPerPage", "pageID"}
                or params["browseType"] != "allstory" or type(params["recPerPage"]) is not int
                or params["recPerPage"] != 50 or type(params["pageID"]) is not int
                or not 1 <= params["pageID"] <= 20):
            raise ValueError("readonly_request_out_of_scope")
        if self._closed:
            raise ValueError("readonly_reader_closed")
        # Copy approved values: no caller-supplied URL or extra parameters reach HTTP.
        request = self._client.build_request("GET", f"/api.php/v2/products/{self._scope.product_id}/stories",
            params={"browseType": "allstory", "recPerPage": 50, "pageID": params["pageID"]},
            headers={"Token": self._token})
        # build_request merges the client's cookie jar. Remove at the final request
        # boundary, including when another concurrent response has populated it.
        request.headers.pop("Cookie", None)
        try:
            response = await self._client.send(request, stream=True, follow_redirects=False)
            try:
                status = response.status_code
                if status == 401:
                    raise ZenTaoAuthenticationError("readonly_authentication_failed")
                if status in {400, 403, 404, 409, 422}:
                    raise ZenTaoRequestRejected("readonly_request_rejected")
                if status == 429 or 500 <= status <= 599:
                    raise ZenTaoUnavailable("readonly_unavailable")
                if status != 200:
                    raise ZenTaoProtocolError("readonly_http_status")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > _MAX_BYTES:
                        raise ZenTaoProtocolError("readonly_response_too_large")
                    content.extend(chunk)
            finally:
                self._client.cookies.clear()
                await response.aclose()
        except httpx.DecodingError:
            raise ZenTaoProtocolError("readonly_invalid_encoding") from None
        except (httpx.TimeoutException, httpx.TransportError):
            raise ZenTaoUnavailable("readonly_unavailable") from None
        try:
            body = json.loads(content.decode("utf-8"), parse_constant=_reject_constant,
                              object_pairs_hook=_unique_object, parse_float=_finite_float)
        except (ValueError, UnicodeError, RecursionError):
            raise ZenTaoProtocolError("readonly_invalid_json") from None
        if not isinstance(body, dict) or not isinstance(body.get("status"), str):
            raise ZenTaoProtocolError("readonly_invalid_envelope")
        if body["status"] != "success":
            raise ZenTaoRejected("readonly_rejected")
        return body
