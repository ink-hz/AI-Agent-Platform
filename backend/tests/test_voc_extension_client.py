from __future__ import annotations

from uuid import UUID

import httpx
import pytest

from app.voc_extension.client import (
    VocExtensionClient,
    VocProtocolError,
    VocUpstreamUnavailable,
)
from app.voc_extension.identity import PlatformVocTokenSigner

SECRET = b"workspace-test-signing-secret-32b"
NOW = 1_777_000_000
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
JTI = UUID("22222222-2222-4222-8222-222222222222")
GOLDEN_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJhdWQiOiJvcmJiZWMtdm9jIiwiY2FwYWJpbGl0aWVzIjpbInZvYy5yZWFkX3NlbGYi"
    "LCJ2b2Muc3VibWl0Il0sImV4cCI6MTc3NzAwMDA2MCwiaWF0IjoxNzc3MDAwMDAwLCJp"
    "c3MiOiJvcmJiZWMtYWdlbnQtcGxhdGZvcm0iLCJqdGkiOiIyMjIyMjIyMi0yMjIyLTQy"
    "MjItODIyMi0yMjIyMjIyMjIyMjIiLCJzdWIiOiIxMTExMTExMS0xMTExLTQxMTEtODEx"
    "MS0xMTExMTExMTExMTEifQ.eSqgFKnIi65OBlfKCzq1AtdeAdBsXbe6VxsRa5xKblg"
)


def test_signer_matches_voc_golden_vector() -> None:
    token = PlatformVocTokenSigner(SECRET).issue(
        USER_ID,
        {"voc.submit", "voc.read_self"},
        now=NOW,
        jti=JTI,
    )

    assert token == GOLDEN_TOKEN


@pytest.mark.parametrize(
    "capabilities",
    [set(), {"voc.read_all"}, {"voc.submit", "voc.read_self", "voc.admin"}],
)
def test_signer_rejects_empty_or_unknown_capabilities(
    capabilities: set[str],
) -> None:
    with pytest.raises(ValueError, match="capabilities"):
        PlatformVocTokenSigner(SECRET).issue(USER_ID, capabilities, now=NOW, jti=JTI)


@pytest.mark.asyncio
async def test_client_uses_fixed_loopback_origin_and_actor_bearer() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": []})

    client = VocExtensionClient(
        "http://127.0.0.1:18130",
        PlatformVocTokenSigner(SECRET),
        transport=httpx.MockTransport(handler),
    )
    try:
        response = await client.request(
            "GET",
            "/api/platform/v1/vocs",
            actor_id=USER_ID,
            query={"query": "发热"},
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert response.body == b'{"items":[]}'
    assert len(requests) == 1
    assert requests[0].url == "http://127.0.0.1:18130/api/platform/v1/vocs?query=%E5%8F%91%E7%83%AD"
    assert requests[0].headers["Authorization"].startswith("Bearer ")


@pytest.mark.asyncio
async def test_client_accepts_only_the_fixed_platform_private_service_address() -> None:
    client = VocExtensionClient(
        "http://172.29.0.3:18130", PlatformVocTokenSigner(SECRET)
    )

    await client.aclose()


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:18130",
        "http://example.test:18130",
        "http://172.30.0.9:18130",
        "http://127.0.0.1:18130/prefix",
        "http://user@127.0.0.1:18130",
    ],
)
def test_client_rejects_any_non_fixed_private_origin(base_url: str) -> None:
    with pytest.raises(ValueError, match="private"):
        VocExtensionClient(base_url, PlatformVocTokenSigner(SECRET))


@pytest.mark.asyncio
async def test_client_rejects_paths_outside_fixed_workspace_contract() -> None:
    client = VocExtensionClient(
        "http://127.0.0.1:18130", PlatformVocTokenSigner(SECRET)
    )
    try:
        with pytest.raises(ValueError, match="path"):
            await client.request("GET", "/health", actor_id=USER_ID)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_client_bounds_responses_and_maps_transport_failures() -> None:
    oversized = VocExtensionClient(
        "http://127.0.0.1:18130",
        PlatformVocTokenSigner(SECRET),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b"x" * 1_048_577)
        ),
    )
    try:
        with pytest.raises(VocProtocolError, match="response_too_large"):
            await oversized.request(
                "GET", "/api/platform/v1/vocs", actor_id=USER_ID
            )
    finally:
        await oversized.aclose()

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private upstream detail", request=request)

    failed = VocExtensionClient(
        "http://127.0.0.1:18130",
        PlatformVocTokenSigner(SECRET),
        transport=httpx.MockTransport(unavailable),
    )
    try:
        with pytest.raises(VocUpstreamUnavailable, match="voc_unavailable"):
            await failed.request("GET", "/api/platform/v1/vocs", actor_id=USER_ID)
    finally:
        await failed.aclose()
