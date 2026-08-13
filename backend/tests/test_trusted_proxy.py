from __future__ import annotations

from ipaddress import ip_network

import pytest
from starlette.requests import Request


def _request(
    peer: str,
    *,
    scheme: str = "http",
    headers: tuple[tuple[bytes, bytes], ...] = (),
) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": scheme,
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": list(headers),
            "client": (peer, 43123),
            "server": ("127.0.0.1", 8080),
        }
    )


TRUSTED = (ip_network("127.0.0.1/32"), ip_network("::1/128"))


def test_loopback_proxy_requires_and_accepts_exact_overwritten_headers() -> None:
    from app.control_plane.client_address import resolve_edge_source

    edge = resolve_edge_source(
        _request(
            "127.0.0.1",
            headers=(
                (b"x-real-ip", b"203.0.113.17"),
                (b"x-forwarded-proto", b"https"),
                (b"forwarded", b""),
            ),
        ),
        TRUSTED,
    )

    assert str(edge.ip) == "203.0.113.17"
    assert edge.scheme == "https"


@pytest.mark.parametrize(
    "headers",
    [
        (),
        ((b"x-real-ip", b"203.0.113.1"),),
        ((b"x-forwarded-proto", b"https"),),
        (
            (b"x-real-ip", b"203.0.113.1, 198.51.100.1"),
            (b"x-forwarded-for", b"203.0.113.1"),
            (b"x-forwarded-proto", b"https"),
        ),
        (
            (b"x-real-ip", b" 203.0.113.1"),
            (b"x-forwarded-for", b"203.0.113.1"),
            (b"x-forwarded-proto", b"https"),
        ),
        (
            (b"x-real-ip", b"fe80::1%eth0"),
            (b"x-forwarded-for", b"fe80::1%eth0"),
            (b"x-forwarded-proto", b"https"),
        ),
        (
            (b"x-real-ip", b"203.0.113.1"),
            (b"x-real-ip", b"198.51.100.1"),
            (b"x-forwarded-for", b"203.0.113.1"),
            (b"x-forwarded-proto", b"https"),
        ),
        (
            (b"x-real-ip", b"203.0.113.1"),
            (b"x-forwarded-for", b"203.0.113.1"),
            (b"x-forwarded-proto", b"https, http"),
        ),
        (
            (b"x-real-ip", b"203.0.113.1"),
            (b"x-forwarded-for", b"203.0.113.1"),
            (b"x-forwarded-proto", b"https"),
            (b"forwarded", b"for=203.0.113.1;proto=https"),
        ),
        (
            (b"x-real-ip", b"203.0.113.1"),
            (b"x-forwarded-proto", b"https"),
            (b"forwarded", b""),
            (b"forwarded", b""),
        ),
    ],
)
def test_trusted_peer_ambiguous_or_missing_forwarding_fails_closed(headers) -> None:
    from app.control_plane.client_address import UntrustedForwardingHeaders
    from app.control_plane.client_address import resolve_edge_source

    with pytest.raises(UntrustedForwardingHeaders):
        resolve_edge_source(_request("127.0.0.1", headers=headers), TRUSTED)


def test_untrusted_peer_ignores_every_forwarding_header_even_when_malformed() -> None:
    from app.control_plane.client_address import resolve_edge_source

    edge = resolve_edge_source(
        _request(
            "198.51.100.9",
            scheme="http",
            headers=(
                (b"x-real-ip", b"not-an-ip, 127.0.0.1"),
                (b"x-forwarded-for", b"127.0.0.1, ::1"),
                (b"x-forwarded-proto", b"https, http"),
                (b"forwarded", b"for=_hidden;proto=https"),
            ),
        ),
        TRUSTED,
    )

    assert str(edge.ip) == "198.51.100.9"
    assert edge.scheme == "http"


def test_trusted_peer_does_not_use_x_forwarded_for_as_an_address_authority() -> None:
    from app.control_plane.client_address import resolve_edge_source

    edge = resolve_edge_source(
        _request(
            "127.0.0.1",
            headers=(
                (b"x-real-ip", b"203.0.113.55"),
                (b"x-forwarded-for", b"198.51.100.1, _obfuscated, bad"),
                (b"x-forwarded-proto", b"https"),
            ),
        ),
        TRUSTED,
    )

    assert str(edge.ip) == "203.0.113.55"
    assert edge.scheme == "https"


def test_ipv4_mapped_loopback_is_trusted_but_cidr_neighbors_are_not() -> None:
    from app.control_plane.client_address import resolve_edge_source

    mapped = resolve_edge_source(
        _request(
            "::ffff:127.0.0.1",
            headers=(
                (b"x-real-ip", b"2001:db8::7"),
                (b"x-forwarded-for", b"2001:db8::7"),
                (b"x-forwarded-proto", b"https"),
            ),
        ),
        TRUSTED,
    )
    neighbor = resolve_edge_source(
        _request(
            "127.0.0.2",
            scheme="http",
            headers=(
                (b"x-real-ip", b"203.0.113.8"),
                (b"x-forwarded-proto", b"https"),
            ),
        ),
        TRUSTED,
    )

    assert str(mapped.ip) == "2001:db8::7"
    assert str(neighbor.ip) == "127.0.0.2"
    assert neighbor.scheme == "http"


@pytest.mark.parametrize("peer", ["unknown", "127.0.0.1%lo0", "_obfuscated"])
def test_malformed_immediate_peer_fails_closed(peer: str) -> None:
    from app.control_plane.client_address import UntrustedForwardingHeaders
    from app.control_plane.client_address import resolve_edge_source

    with pytest.raises(UntrustedForwardingHeaders):
        resolve_edge_source(_request(peer), TRUSTED)
