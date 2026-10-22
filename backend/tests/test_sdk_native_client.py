from __future__ import annotations

from contextlib import contextmanager
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.control_plane.models import AuthContext, Role
from app.sdk_workbench.native_client import NativeZenTaoClient
from app.sdk_workbench.sso import SdkSsoService
from app.sdk_workbench.zentao import (
    ZenTaoAuthenticationError,
    ZenTaoProtocolError,
    ZenTaoRequestRejected,
    ZenTaoUnavailable,
)


ALICE = UUID("11111111-1111-4111-8111-111111111111")
BOB = UUID("22222222-2222-4222-8222-222222222222")


class Identities:
    def __init__(self) -> None:
        self.members = {
            ALICE: {"eligible": True, "account": "alice", "provision_state": "bound"},
            BOB: {"eligible": True, "account": "bob", "provision_state": "bound"},
        }
        self.timestamp = 1792471200

    def identity(self, user_id: UUID) -> dict | None:
        return self.members.get(user_id)

    @contextmanager
    def login_slot(self):
        self.timestamp += 1
        yield self.timestamp


def actor(user_id: UUID = ALICE, *, stale: bool = False) -> AuthContext:
    return AuthContext(user_id, Role.MEMBER, uuid4(), stale)


def build_client(tmp_path, repository, handler):
    key = tmp_path / "entry-key"
    key.write_text("a" * 32, encoding="ascii")
    transport = httpx.MockTransport(handler)
    sso = SdkSsoService(
        repository,
        private_origin="http://zentao:80",
        public_origin="https://47.106.112.69",
        platform_origin="https://agent.orbbec.com.cn",
        entry_code="sdk-sso",
        entry_key_file=key,
        transport=transport,
    )
    return NativeZenTaoClient(sso, transport=transport)


@pytest.mark.asyncio
async def test_personal_tokens_are_separate_and_cached_per_actor(tmp_path):
    repository = Identities()
    logins: list[str] = []
    calls: list[str] = []
    sids = {"alice": "a" * 32, "bob": "b" * 32}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api.php":
            account = request.url.params["account"]
            logins.append(account)
            return httpx.Response(302, headers={"location": "/", "set-cookie": f"zentaosid={sids[account]}; Path=/; HttpOnly"})
        calls.append(request.headers["Token"])
        return httpx.Response(200, json={"status": "success", "story": {"id": 3}})

    client = build_client(tmp_path, repository, respond)
    try:
        assert (await client.request(actor(ALICE), "GET", "stories/3"))["story"]["id"] == 3
        await client.request(actor(BOB), "GET", "stories/3")
        await client.request(actor(ALICE), "GET", "stories/3")
    finally:
        await client.close()
    assert logins == ["alice", "bob"]
    assert calls == [f"ss_{sids['alice']}", f"ss_{sids['bob']}", f"ss_{sids['alice']}"]


@pytest.mark.asyncio
async def test_entitlement_is_rechecked_before_cached_session_use(tmp_path):
    repository = Identities()
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api.php":
            return httpx.Response(302, headers={"location": "/", "set-cookie": f"zentaosid={'a' * 32}; Path=/"})
        return httpx.Response(200, json={"status": "success"})

    client = build_client(tmp_path, repository, respond)
    try:
        await client.request(actor(), "GET", "stories/3")
        repository.members[ALICE]["eligible"] = False
        with pytest.raises(HTTPException) as denied:
            await client.request(actor(), "GET", "stories/3")
    finally:
        await client.close()
    assert denied.value.status_code == 403
    assert seen == ["/api.php", "/api.php/v2/stories/3"]


@pytest.mark.asyncio
async def test_stale_identity_is_denied_before_session_or_api_call(tmp_path):
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        raise AssertionError("no ZenTao traffic expected")

    client = build_client(tmp_path, Identities(), respond)
    try:
        with pytest.raises(HTTPException) as denied:
            await client.request(actor(stale=True), "GET", "stories/3")
    finally:
        await client.close()
    assert denied.value.status_code == 503
    assert seen == []


@pytest.mark.asyncio
async def test_invalid_personal_session_relogs_once(tmp_path):
    repository = Identities()
    login_count = 0
    tokens: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        if request.url.path == "/api.php":
            login_count += 1
            sid = f"{login_count:032x}"
            return httpx.Response(302, headers={"location": "/", "set-cookie": f"zentaosid={sid}; Path=/"})
        tokens.append(request.headers["Token"])
        if login_count == 1:
            return httpx.Response(401, json={"status": "fail"})
        return httpx.Response(200, json={"status": "success", "story": {"id": 3}})

    client = build_client(tmp_path, repository, respond)
    try:
        assert (await client.request(actor(), "GET", "stories/3"))["story"]["id"] == 3
    finally:
        await client.close()
    assert login_count == 2
    assert tokens == [f"ss_{1:032x}", f"ss_{2:032x}"]


@pytest.mark.asyncio
async def test_second_authentication_failure_stops_without_third_login(tmp_path):
    logins = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/api.php":
            logins += 1
            return httpx.Response(302, headers={"location": "/", "set-cookie": f"zentaosid={logins:032x}; Path=/"})
        return httpx.Response(401, json={"status": "fail"})

    client = build_client(tmp_path, Identities(), respond)
    try:
        with pytest.raises(ZenTaoAuthenticationError):
            await client.request(actor(), "GET", "stories/3")
    finally:
        await client.close()
    assert logins == 2


@pytest.mark.asyncio
async def test_zen_permission_denial_does_not_relog(tmp_path):
    logins = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/api.php":
            logins += 1
            return httpx.Response(302, headers={"location": "/", "set-cookie": f"zentaosid={'a' * 32}; Path=/"})
        return httpx.Response(403, json={"status": "fail"})

    client = build_client(tmp_path, Identities(), respond)
    try:
        with pytest.raises(ZenTaoRequestRejected):
            await client.request(actor(), "GET", "stories/3")
    finally:
        await client.close()
    assert logins == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_path", ["https://evil.invalid/", "//evil.invalid", "/stories/3", "stories/../3", "stories/3?x=y"])
async def test_request_rejects_unbounded_api_paths(tmp_path, invalid_path):
    client = build_client(tmp_path, Identities(), lambda _: pytest.fail("no network expected"))
    try:
        with pytest.raises(ValueError, match="zentao_invalid_path"):
            await client.request(actor(), "GET", invalid_path)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_malformed_and_large_responses_are_controlled(tmp_path):
    cases = [httpx.Response(200, content=b"invalid"), httpx.Response(200, content=b"x" * 1_048_577)]
    errors = []

    for response in cases:
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api.php":
                return httpx.Response(302, headers={"location": "/", "set-cookie": f"zentaosid={'a' * 32}; Path=/"})
            return response

        client = build_client(tmp_path, Identities(), respond)
        try:
            with pytest.raises(ZenTaoProtocolError) as error:
                await client.request(actor(), "GET", "stories/3")
            errors.append(str(error.value))
        finally:
            await client.close()
    assert errors == ["zentao_invalid_json", "zentao_response_too_large"]


@pytest.mark.asyncio
async def test_v2_response_requires_zentao_status_envelope(tmp_path):
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api.php":
            return httpx.Response(302, headers={"location": "/", "set-cookie": f"zentaosid={'a' * 32}; Path=/"})
        return httpx.Response(200, json={"story": {"id": 3}})

    client = build_client(tmp_path, Identities(), respond)
    try:
        with pytest.raises(ZenTaoProtocolError, match="zentao_invalid_envelope"):
            await client.request(actor(), "GET", "stories/3")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_transport_timeout_is_controlled(tmp_path):
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api.php":
            return httpx.Response(302, headers={"location": "/", "set-cookie": f"zentaosid={'a' * 32}; Path=/"})
        raise httpx.ReadTimeout("private API timeout")

    client = build_client(tmp_path, Identities(), respond)
    try:
        with pytest.raises(ZenTaoUnavailable):
            await client.request(actor(), "GET", "stories/3")
    finally:
        await client.close()
