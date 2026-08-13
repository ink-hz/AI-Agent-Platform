from __future__ import annotations

from dataclasses import dataclass
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
)
from typing import Literal

from starlette.requests import Request


class UntrustedForwardingHeaders(ValueError):
    """The immediate trusted proxy did not provide one canonical hop."""


@dataclass(frozen=True)
class EdgeSource:
    ip: IPv4Address | IPv6Address
    scheme: Literal["http", "https"]


def _header_values(request: Request, name: bytes) -> list[str]:
    values: list[str] = []
    for raw_name, raw_value in request.scope.get("headers", ()):
        if raw_name.lower() != name:
            continue
        try:
            values.append(raw_value.decode("ascii"))
        except UnicodeDecodeError:
            raise UntrustedForwardingHeaders("forwarding headers invalid") from None
    return values


def _single_header(request: Request, name: bytes) -> str | None:
    values = _header_values(request, name)
    if len(values) != 1:
        return None
    return values[0]


def parse_single_ip(value: str | None) -> IPv4Address | IPv6Address:
    if (
        value is None
        or not value
        or value != value.strip()
        or "," in value
        or "%" in value
        or any(character.isspace() for character in value)
    ):
        raise UntrustedForwardingHeaders("forwarding headers invalid")
    try:
        parsed = ip_address(value)
    except ValueError:
        raise UntrustedForwardingHeaders("forwarding headers invalid") from None
    if isinstance(parsed, IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


def parse_exact_scheme(value: str | None) -> Literal["http", "https"]:
    if value not in {"http", "https"}:
        raise UntrustedForwardingHeaders("forwarding headers invalid")
    return value


def _normalized_peer(request: Request) -> IPv4Address | IPv6Address:
    client = request.client
    if client is None:
        raise UntrustedForwardingHeaders("client address unavailable")
    return parse_single_ip(client.host)


def resolve_edge_source(
    request: Request,
    trusted: tuple[IPv4Network | IPv6Network, ...],
) -> EdgeSource:
    peer = _normalized_peer(request)
    is_trusted = any(
        peer.version == network.version and peer in network for network in trusted
    )
    if not is_trusted:
        scheme = request.url.scheme
        if scheme not in {"http", "https"}:
            raise UntrustedForwardingHeaders("request scheme invalid")
        return EdgeSource(ip=peer, scheme=scheme)

    real_ip = parse_single_ip(_single_header(request, b"x-real-ip"))
    scheme = parse_exact_scheme(_single_header(request, b"x-forwarded-proto"))
    forwarded = _header_values(request, b"forwarded")
    if len(forwarded) > 1 or any(value != "" for value in forwarded):
        raise UntrustedForwardingHeaders("forwarding headers invalid")
    return EdgeSource(ip=real_ip, scheme=scheme)
