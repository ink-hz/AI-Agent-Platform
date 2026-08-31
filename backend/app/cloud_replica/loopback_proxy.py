from __future__ import annotations

import hmac
import os
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.control_plane.client_address import (
    EdgeSource,
    UntrustedForwardingHeaders,
    resolve_edge_source,
)
from app.local_secrets import SecretFileUnavailable, read_secret_file

_HOP_BY_HOP = frozenset(
    {
        b"connection",
        b"keep-alive",
        b"proxy-authenticate",
        b"proxy-authorization",
        b"proxy-connection",
        b"te",
        b"trailer",
        b"transfer-encoding",
        b"upgrade",
    }
)
_REPLACED = frozenset(
    {
        b"authorization",
        b"forwarded",
        b"x-forwarded-for",
        b"x-forwarded-proto",
        b"x-real-ip",
    }
)
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_OFFICE_RECIPIENT_ROUTES = frozenset(
    {
        ("POST", "/api/v1/internal/office/recipient-directory/search"),
        ("POST", "/api/v1/internal/office/recipient-directory/resolve"),
        ("GET", "/api/v1/internal/office/recipient-directory/departments"),
    }
)


def _exact_networks(value: str):
    try:
        networks = tuple(
            ip_network(item.strip(), strict=True)
            for item in value.split(",")
            if item.strip()
        )
    except ValueError as error:
        raise RuntimeError("loopback proxy trusted peers invalid") from error
    if not networks or not all(network.num_addresses == 1 for network in networks):
        raise RuntimeError("loopback proxy trusted peers must be exact hosts")
    return networks


def _target(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("loopback proxy target invalid")
    return value.rstrip("/")


class LoopbackProxy:
    """HTTP boundary that trusts one immediate peer and rewrites proxy headers."""

    def __init__(
        self,
        *,
        target_base_url: str,
        trusted_peer_cidrs: str,
        source_address: str | None = None,
        office_recipient_bearer: str | None = None,
    ) -> None:
        self.target_base_url = _target(target_base_url)
        self.trusted_peers = _exact_networks(trusted_peer_cidrs)
        if source_address:
            try:
                source_address = ip_address(source_address).compressed
            except ValueError as error:
                raise RuntimeError("loopback proxy source address invalid") from error
        self.source_address = source_address
        if office_recipient_bearer is not None and (
            not isinstance(office_recipient_bearer, str)
            or len(office_recipient_bearer.encode("utf-8")) < 32
        ):
            raise RuntimeError("loopback Office recipient bearer invalid")
        self._office_authorization = (
            f"Bearer {office_recipient_bearer}".encode()
            if office_recipient_bearer is not None
            else None
        )

    async def _lifespan(self, receive, send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    @staticmethod
    async def _request_body(receive):
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body = message.get("body", b"")
            if body:
                yield body
            more = message.get("more_body", False)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":
            await JSONResponse({"detail": "unsupported"}, status_code=400)(
                scope, receive, send
            )
            return
        request = Request(scope, receive)
        try:
            peer = ip_address(request.client.host if request.client else "")
            if not any(
                peer.version == network.version and peer in network
                for network in self.trusted_peers
            ):
                raise UntrustedForwardingHeaders("proxy peer rejected")
            forwarding_names = {
                name.lower()
                for name, _ in scope.get("headers", ())
                if name.lower()
                in {
                    b"forwarded",
                    b"x-forwarded-for",
                    b"x-forwarded-proto",
                    b"x-real-ip",
                }
            }
            if not forwarding_names:
                scheme = request.url.scheme
                if scheme not in {"http", "https"}:
                    raise UntrustedForwardingHeaders("request scheme invalid")
                edge = EdgeSource(ip=peer, scheme=scheme)
            else:
                edge = resolve_edge_source(request, self.trusted_peers)
        except (ValueError, UntrustedForwardingHeaders):
            await JSONResponse(
                {"detail": "request forwarding rejected"}, status_code=400
            )(scope, receive, send)
            return

        method = scope["method"].upper()
        path = scope.get("path", "")
        try:
            canonical_raw_path = path.encode("ascii")
        except (AttributeError, UnicodeError):
            canonical_raw_path = b""
        office_request = (
            (method, path) in _OFFICE_RECIPIENT_ROUTES
            and scope.get("raw_path") == canonical_raw_path
        )
        if office_request and self._office_authorization is not None:
            supplied_authorization = [
                value
                for name, value in scope.get("headers", ())
                if name.lower() == b"authorization"
            ]
            if len(supplied_authorization) != 1 or not hmac.compare_digest(
                supplied_authorization[0], self._office_authorization
            ):
                await JSONResponse(
                    {"detail": "not found"},
                    status_code=404,
                    headers=_NO_STORE,
                )(scope, receive, send)
                return

        headers = [
            (name, value)
            for name, value in scope.get("headers", ())
            if name.lower() not in _HOP_BY_HOP and name.lower() not in _REPLACED
        ]
        canonical_ip = str(edge.ip).encode("ascii")
        headers.extend(
            (
                (b"x-real-ip", canonical_ip),
                (b"x-forwarded-for", canonical_ip),
                (b"x-forwarded-proto", edge.scheme.encode("ascii")),
            )
        )
        if office_request and self._office_authorization is not None:
            headers.append((b"authorization", self._office_authorization))
        transport = httpx.AsyncHTTPTransport(local_address=self.source_address)
        client = httpx.AsyncClient(transport=transport, timeout=None)
        upstream = None
        try:
            target = self.target_base_url + scope.get("raw_path", b"/").decode(
                "ascii", "strict"
            )
            query = scope.get("query_string", b"")
            if query:
                target += "?" + query.decode("ascii", "strict")
            upstream_request = client.build_request(
                scope["method"],
                target,
                headers=headers,
                content=self._request_body(receive),
            )
            upstream = await client.send(upstream_request, stream=True)
            response_headers = [
                (name, value)
                for name, value in upstream.headers.raw
                if name.lower() not in _HOP_BY_HOP
            ]
            await send(
                {
                    "type": "http.response.start",
                    "status": upstream.status_code,
                    "headers": response_headers,
                }
            )
            async for chunk in upstream.aiter_raw():
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": True}
                )
            await send({"type": "http.response.body", "body": b""})
        except (httpx.HTTPError, UnicodeError, ValueError):
            if upstream is None:
                await JSONResponse(
                    {"detail": "upstream unavailable"}, status_code=502
                )(scope, receive, send)
        finally:
            if upstream is not None:
                await upstream.aclose()
            await client.aclose()


def create_app() -> LoopbackProxy:
    office_enabled = os.getenv(
        "PLATFORM_OFFICE_RECIPIENT_DIRECTORY_ENABLED", "0"
    ) not in {"0", "false", "False"}
    office_bearer = None
    if office_enabled:
        bearer_file = os.getenv("PLATFORM_OFFICE_RECIPIENT_BEARER_FILE", "").strip()
        if not bearer_file:
            raise RuntimeError("loopback Office recipient bearer file required")
        try:
            office_bearer = read_secret_file(bearer_file)
        except SecretFileUnavailable:
            raise RuntimeError("loopback Office recipient bearer unavailable") from None
    return LoopbackProxy(
        target_base_url=os.getenv(
            "PLATFORM_LOOPBACK_TARGET_BASE_URL", "http://platform-api:8080"
        ),
        trusted_peer_cidrs=os.getenv(
            "PLATFORM_LOOPBACK_TRUSTED_PROXY_CIDRS", "127.0.0.1/32,::1/128"
        ),
        source_address=os.getenv("PLATFORM_LOOPBACK_SOURCE_ADDRESS") or None,
        office_recipient_bearer=office_bearer,
    )
