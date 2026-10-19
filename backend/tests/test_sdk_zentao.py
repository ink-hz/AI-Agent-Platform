from __future__ import annotations

import json

import httpx
import pytest

from app.sdk_workbench.zentao import (
    ZenTaoClient,
    ZenTaoProtocolError,
    ZenTaoRejected,
    ZenTaoUnavailable,
    ZenTaoAuthenticationError,
    ZenTaoRequestRejected,
)


@pytest.fixture
def password_file(tmp_path):
    path = tmp_path / "password"
    path.write_text("secret-password\n")
    return path


@pytest.mark.asyncio
async def test_login_and_exact_story_task_contract(password_file):
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, dict(request.url.params), request.headers.get("token"), request.content))
        if request.url.path.endswith("/users/login"):
            assert json.loads(request.content) == {"account": "sdk-api", "password": "secret-password"}
            return httpx.Response(200, json={"status": "success", "token": "private-token"})
        if request.url.path.endswith("/products/7/stories"):
            return httpx.Response(200, json={"status": "success", "stories": [{"id": "12", "title": "USB 稳定性"}], "page": 2, "total": 1})
        if request.url.path.endswith("/stories/12"):
            return httpx.Response(200, json={"status": "success", "story": {"id": "12", "title": "USB 稳定性", "verify": "连续运行"}})
        if request.url.path.endswith("/tasks/31"):
            return httpx.Response(200, json={"status": "success", "task": {"id": "31", "story": "12", "execution": "9", "name": "修复断流"}})
        if request.url.path.endswith("/stories"):
            assert json.loads(request.content) == {"productID": 7, "title": "USB 稳定性", "spec": "客户现场", "verify": "连续运行"}
            return httpx.Response(200, json={"status": "success", "id": 12})
        if request.url.path.endswith("/tasks"):
            assert json.loads(request.content) == {"executionID": 9, "story": 12, "name": "修复断流", "desc": "检查枚举"}
            return httpx.Response(200, json={"status": "success", "id": 31})
        raise AssertionError(request.url)

    client = ZenTaoClient("http://127.0.0.1:18131", "sdk-api", password_file, transport=httpx.MockTransport(respond))
    try:
        assert (await client.list_stories(7, 2))["stories"][0]["id"] == "12"
        assert (await client.get_story(12))["title"] == "USB 稳定性"
        assert (await client.get_task(31))["story"] == "12"
        assert await client.create_story(7, "USB 稳定性", "客户现场", "连续运行") == 12
        assert await client.create_task(9, 12, "修复断流", "检查枚举") == 31
    finally:
        await client.close()

    assert calls[0][1] == "/api.php/v2/users/login"
    assert calls[1][1:4] == ("/api.php/v2/products/7/stories", {"browseType": "allstory", "recPerPage": "50", "pageID": "2"}, "private-token")
    assert all(call[3] == "private-token" for call in calls[1:])


@pytest.mark.asyncio
async def test_rejects_redirect_bad_shape_and_oversized_response(password_file):
    responses = iter([
        httpx.Response(200, json={"status": "success", "token": "private-token"}),
        httpx.Response(302, headers={"location": "https://evil.example/token"}),
        httpx.Response(200, json={"status": "success", "id": 0}),
        httpx.Response(200, content=b"x" * 1_048_577),
    ])
    client = ZenTaoClient("http://127.0.0.1:18131", "sdk-api", password_file, transport=httpx.MockTransport(lambda request: next(responses)))
    try:
        with pytest.raises(ZenTaoProtocolError):
            await client.get_story(12)
        with pytest.raises(ZenTaoProtocolError):
            await client.create_story(7, "t", "s", "v")
        with pytest.raises(ZenTaoProtocolError):
            await client.get_story(12)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_upstream_failure_is_explicit(password_file):
    def respond(request):
        if request.url.path.endswith("/users/login"):
            return httpx.Response(200, json={"status": "success", "token": "token"})
        return httpx.Response(200, json={"status": "fail", "message": "internal private detail"})

    client = ZenTaoClient("http://127.0.0.1:18131", "sdk-api", password_file, transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ZenTaoRejected):
            await client.get_story(12)
    finally:
        await client.close()


def test_requires_fixed_private_origin_and_password_file(password_file):
    with pytest.raises(ValueError):
        ZenTaoClient("https://example.com", "sdk-api", password_file)
    with pytest.raises(ValueError):
        ZenTaoClient("http://127.0.0.1:18131/attacker", "sdk-api", password_file)
    with pytest.raises(ValueError):
        ZenTaoClient("http://169.254.169.254:80", "sdk-api", password_file)
    with pytest.raises(ValueError):
        ZenTaoClient("http://127.0.0.1:18131", "sdk-api", password_file.parent / "missing")


@pytest.mark.asyncio
async def test_expired_token_reauthenticates_once_and_persistent_401_is_definite(password_file):
    logins = 0
    def respond(request):
        nonlocal logins
        if request.url.path.endswith("/users/login"):
            logins += 1
            return httpx.Response(200, json={"status": "success", "token": f"token-{logins}"})
        if request.headers.get("token") == "token-1":
            return httpx.Response(401, json={"status": "fail"})
        return httpx.Response(200, json={"status": "success", "story": {"id": "12"}})
    client = ZenTaoClient("http://127.0.0.1:18131", "sdk-api", password_file, transport=httpx.MockTransport(respond))
    try:
        assert (await client.get_story(12))["id"] == "12"
        assert logins == 2
    finally:
        await client.close()
    failing = ZenTaoClient("http://127.0.0.1:18131", "sdk-api", password_file,
                           transport=httpx.MockTransport(lambda request: httpx.Response(401, json={"status": "fail"})))
    try:
        with pytest.raises(ZenTaoAuthenticationError):
            await failing.create_story(7, "t", "s", "v")
    finally:
        await failing.close()


@pytest.mark.asyncio
async def test_permission_denial_is_a_definite_rejection(password_file):
    def respond(request):
        if request.url.path.endswith("/users/login"):
            return httpx.Response(200, json={"status": "success", "token": "token"})
        return httpx.Response(403, json={"status": "fail"})
    client = ZenTaoClient("http://127.0.0.1:18131", "sdk-api", password_file, transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ZenTaoRequestRejected):
            await client.create_story(7, "t", "s", "v")
    finally:
        await client.close()
