from __future__ import annotations

import asyncio
import json
import logging
import traceback

import httpx
import pytest
import respx

from app.control_plane.dingtalk import (
    DingTalkAuthResult,
    DingTalkClient,
    DingTalkDepartment,
    DingTalkMember,
    DingTalkProviderError,
)


API = "https://api.test.invalid"
OAPI = "https://oapi.test.invalid"


def _client(*, flow: str = "in_client", **overrides) -> DingTalkClient:
    return DingTalkClient(
        app_key="test-app-key",
        app_secret="test-app-secret",
        corp_id="test-corp",
        login_flow=flow,
        api_base_url=API,
        oapi_base_url=OAPI,
        **overrides,
    )


def _token() -> httpx.Response:
    return httpx.Response(200, json={"accessToken": "provider-token", "expireIn": 7200})


@pytest.mark.asyncio
@respx.mock
async def test_application_token_is_cached_and_refreshed_before_expiry() -> None:
    clock = [100.0]
    route = respx.post(f"{API}/v1.0/oauth2/accessToken").mock(side_effect=[
        _token(),
        httpx.Response(200, json={"accessToken": "replacement", "expireIn": 7200}),
    ])
    member = respx.post(f"{OAPI}/topapi/v2/user/get").mock(
        return_value=httpx.Response(200, json={
            "errcode": 0,
            "errmsg": "ok",
            "result": {
                "userid": "employee-1",
                "unionid": "union-1",
                "name": "Employee",
                "active": True,
                "dept_id_list": [1, 2],
            },
        })
    )
    client = _client(now=lambda: clock[0])

    assert await client.get_member("employee-1") == DingTalkMember(
        "employee-1", "union-1", "Employee", True, (1, 2)
    )
    await client.get_member("employee-1")
    assert route.call_count == 1
    clock[0] += 6901
    await client.get_member("employee-1")
    assert route.call_count == 2
    assert member.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_concurrent_requests_share_one_locked_token_acquisition() -> None:
    token_route = respx.post(f"{API}/v1.0/oauth2/accessToken").mock(
        return_value=_token()
    )
    respx.post(f"{OAPI}/topapi/v2/user/get").mock(
        return_value=httpx.Response(200, json={
            "errcode": 0, "errmsg": "ok", "result": {
                "userid": "employee-1", "unionid": "union-1",
                "name": "Employee", "active": True, "dept_id_list": [1],
            }
        })
    )
    client = _client()

    await asyncio.gather(*(client.get_member("employee-1") for _ in range(8)))

    assert token_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_in_client_code_exchange_returns_typed_identity_without_retry() -> None:
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    route = respx.post(f"{OAPI}/topapi/v2/user/getuserinfo").mock(
        return_value=httpx.Response(200, json={
            "errcode": 0,
            "errmsg": "ok",
            "result": {"userid": "employee-1", "unionid": "union-1"},
        })
    )
    client = _client()

    assert await client.exchange_login_code("one-time-code") == DingTalkAuthResult(
        unionid="union-1", userid="employee-1", corp_id="test-corp"
    )
    request = route.calls[0].request
    assert request.headers["X-Request-Id"]
    assert json.loads(request.content) == {"code": "one-time-code"}


@pytest.mark.asyncio
@respx.mock
async def test_qr_oauth_code_exchange_gets_unionid_and_corp() -> None:
    token_route = respx.post(f"{API}/v1.0/oauth2/userAccessToken").mock(
        return_value=httpx.Response(200, json={
            "accessToken": "user-token", "refreshToken": "unused",
            "expireIn": 7200, "corpId": "test-corp",
        })
    )
    me_route = respx.get(f"{API}/v1.0/contact/users/me").mock(
        return_value=httpx.Response(200, json={
            "nick": "Display only", "openId": "unused", "unionId": "union-1"
        })
    )
    client = _client(flow="qr")

    result = await client.exchange_login_code("one-time-code")

    assert result == DingTalkAuthResult("union-1", None, "test-corp")
    assert token_route.call_count == 1
    assert me_route.calls[0].request.headers["x-acs-dingtalk-access-token"] == "user-token"


@pytest.mark.asyncio
@respx.mock
async def test_qr_exchange_rejects_wrong_corporation_before_profile_read() -> None:
    respx.post(f"{API}/v1.0/oauth2/userAccessToken").mock(
        return_value=httpx.Response(200, json={
            "accessToken": "user-token", "refreshToken": "unused",
            "expireIn": 7200, "corpId": "other-corp",
        })
    )
    profile = respx.get(f"{API}/v1.0/contact/users/me").mock(
        return_value=httpx.Response(500)
    )

    with pytest.raises(DingTalkProviderError, match="organization mismatch"):
        await _client(flow="qr").exchange_login_code("one-time-code")

    assert profile.call_count == 0


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("flow", ["qr", "in_client"])
@pytest.mark.parametrize("status", [429, 503])
async def test_login_code_exchange_is_never_retried_on_429_or_5xx(
    flow: str,
    status: int,
) -> None:
    if flow == "in_client":
        respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    endpoint = (
        f"{API}/v1.0/oauth2/userAccessToken"
        if flow == "qr"
        else f"{OAPI}/topapi/v2/user/getuserinfo"
    )
    route = respx.post(endpoint).mock(
        return_value=httpx.Response(status, json={"code": "serviceUnavailable"})
    )

    with pytest.raises(DingTalkProviderError):
        await _client(flow=flow).exchange_login_code("one-time-code")

    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_union_resolution_rejects_external_or_absent_contact() -> None:
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    route = respx.post(f"{OAPI}/topapi/user/getbyunionid").mock(
        return_value=httpx.Response(200, json={
            "errcode": "0", "errmsg": "ok",
            "result": {"contact_type": 1, "userid": "external"},
        })
    )

    with pytest.raises(DingTalkProviderError, match="member unavailable"):
        await _client().resolve_union_member("union-1")

    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_get_member_preserves_inactive_state_and_strictly_parses_response() -> None:
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    route = respx.post(f"{OAPI}/topapi/v2/user/get").mock(side_effect=[
        httpx.Response(200, json={
            "errcode": 0, "errmsg": "ok", "result": {
                "userid": "employee-1", "unionid": "union-1", "name": "Employee",
                "active": False, "dept_id_list": [1],
            }
        }),
        httpx.Response(200, json={
            "errcode": 0, "errmsg": "ok", "result": {
                "userid": 17, "unionid": "union-1", "name": "Employee",
                "active": True, "dept_id_list": [1],
            }
        }),
    ])
    client = _client()

    assert (await client.get_member("employee-1")).active is False
    with pytest.raises(DingTalkProviderError, match="response invalid"):
        await client.get_member("employee-1")
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_department_tree_and_member_pages_are_typed_async_iterators() -> None:
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    departments = respx.post(f"{OAPI}/topapi/v2/department/listsub").mock(
        side_effect=[
            httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "result": [
                {"dept_id": 2, "parent_id": 1, "name": "Engineering"}
            ]}),
            httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "result": []}),
        ]
    )
    members = respx.post(f"{OAPI}/topapi/v2/user/list").mock(side_effect=[
        httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "result": {
            "has_more": True, "next_cursor": 100, "list": [{
                "userid": "employee-1", "unionid": "union-1", "name": "One",
                "active": True, "dept_id_list": [2],
            }]
        }}),
        httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "result": {
            "has_more": False, "next_cursor": 0, "list": [{
                "userid": "employee-2", "unionid": "union-2", "name": "Two",
                "active": True, "dept_id_list": [2, 3],
            }]
        }}),
    ])
    client = _client()

    assert [item async for item in client.iter_departments()] == [
        DingTalkDepartment(2, 1, "Engineering")
    ]
    assert [item async for item in client.iter_department_members(2)] == [
        DingTalkMember("employee-1", "union-1", "One", True, (2,)),
        DingTalkMember("employee-2", "union-2", "Two", True, (2, 3)),
    ]
    assert departments.call_count == 2
    bodies = [json.loads(call.request.content) for call in members.calls]
    assert [body["cursor"] for body in bodies] == [0, 100]
    assert all(body["size"] == 100 for body in bodies)


@pytest.mark.asyncio
@respx.mock
async def test_idempotent_read_retries_only_429_and_5xx_with_capped_retry_after() -> None:
    sleeps: list[float] = []
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    route = respx.post(f"{OAPI}/topapi/v2/user/get").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "999"}, json={"errcode": 88}),
        httpx.Response(503, json={"errcode": -1}),
        httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "result": {
            "userid": "employee-1", "unionid": "union-1", "name": "Employee",
            "active": True, "dept_id_list": [1],
        }}),
    ])
    client = _client(sleep=lambda seconds: sleeps.append(seconds))

    assert (await client.get_member("employee-1")).userid == "employee-1"
    assert route.call_count == 3
    assert sleeps == [2.0, 0.2]


@pytest.mark.asyncio
@respx.mock
async def test_provider_errors_and_logs_are_redacted(caplog) -> None:
    secrets = [
        "one-time-code", "provider-token", "employee-sensitive",
        "union-sensitive", "test-app-secret", "13800000000", "private@example.com",
    ]
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    route = respx.post(f"{OAPI}/topapi/v2/user/get").mock(
        return_value=httpx.Response(400, json={
            "errcode": "invalid.user",
            "errmsg": "employee-sensitive union-sensitive test-app-secret "
                      "13800000000 private@example.com provider-token",
        })
    )
    caplog.set_level(logging.WARNING)

    with pytest.raises(DingTalkProviderError) as caught:
        await _client().get_member("employee-sensitive")

    rendered = str(caught.value) + repr(caught.value) + caplog.text
    assert "invalid.user" in rendered
    assert route.calls[0].request.headers["X-Request-Id"] in rendered
    assert all(secret not in rendered for secret in secrets)


def test_client_uses_explicit_bounded_httpx_timeouts() -> None:
    client = _client()
    timeout = client.timeout
    assert timeout.connect == 2.0
    assert timeout.read == 5.0
    assert timeout.write == 5.0
    assert timeout.pool == 2.0


def test_provider_dto_representations_do_not_expose_identity_values() -> None:
    values = ("employee-sensitive", "union-sensitive", "corp-sensitive", "Name")
    rendered = repr(DingTalkMember(values[0], values[1], values[3], True, (1,)))
    rendered += repr(DingTalkAuthResult(values[1], values[0], values[2]))

    assert all(value not in rendered for value in values)


@pytest.mark.asyncio
@respx.mock
async def test_qr_profile_read_retries_without_repeating_code_exchange() -> None:
    exchange = respx.post(f"{API}/v1.0/oauth2/userAccessToken").mock(
        return_value=httpx.Response(200, json={
            "accessToken": "user-token", "expireIn": 7200,
            "corpId": "test-corp",
        })
    )
    profile = respx.get(f"{API}/v1.0/contact/users/me").mock(side_effect=[
        httpx.Response(503, json={"code": "temporarilyUnavailable"}),
        httpx.Response(200, json={"unionId": "union-1"}),
    ])
    sleeps: list[float] = []

    result = await _client(
        flow="qr", sleep=lambda seconds: sleeps.append(seconds)
    ).exchange_login_code("single-use-code")

    assert result.unionid == "union-1"
    assert exchange.call_count == 1
    assert profile.call_count == 2
    assert sleeps == [0.1]


@pytest.mark.asyncio
@respx.mock
async def test_response_parse_error_preserves_exact_outbound_request_id() -> None:
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    route = respx.post(f"{OAPI}/topapi/v2/user/get").mock(
        return_value=httpx.Response(200, json={
            "errcode": 0, "result": {
                "userid": 17, "unionid": "union-1", "name": "Employee",
                "active": True, "dept_id_list": [1],
            },
        })
    )

    with pytest.raises(DingTalkProviderError) as caught:
        await _client().get_member("employee-1")

    assert caught.value.request_id == route.calls[0].request.headers["X-Request-Id"]


@pytest.mark.asyncio
@respx.mock
async def test_department_listsub_uses_only_supported_body_fields() -> None:
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    route = respx.post(f"{OAPI}/topapi/v2/department/listsub").mock(
        return_value=httpx.Response(
            200, json={"errcode": 0, "errmsg": "ok", "result": []}
        )
    )

    assert [item async for item in _client().iter_departments()] == []
    assert json.loads(route.calls[0].request.content) == {
        "dept_id": 1,
        "language": "zh_CN",
    }


@pytest.mark.asyncio
@respx.mock
async def test_official_invalid_token_response_refreshes_once_with_global_cap() -> None:
    token_route = respx.post(f"{API}/v1.0/oauth2/accessToken").mock(side_effect=[
        httpx.Response(200, json={"accessToken": "expired-token", "expireIn": 7200}),
        httpx.Response(200, json={"accessToken": "new-token", "expireIn": 7200}),
    ])
    member_route = respx.post(f"{OAPI}/topapi/v2/user/get").mock(side_effect=[
        httpx.Response(200, json={"errcode": 42001, "errmsg": "expired"}),
        httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "result": {
            "userid": "employee-1", "unionid": "union-1", "name": "Employee",
            "active": True, "dept_id_list": [1],
        }}),
    ])

    assert (await _client().get_member("employee-1")).userid == "employee-1"
    assert token_route.call_count == 2
    assert member_route.call_count == 2
    assert [call.request.url.params["access_token"] for call in member_route.calls] == [
        "expired-token", "new-token"
    ]


@pytest.mark.asyncio
@respx.mock
async def test_invalid_token_refresh_does_not_exceed_three_business_attempts() -> None:
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(side_effect=[
        httpx.Response(200, json={"accessToken": "token-1", "expireIn": 7200}),
        httpx.Response(200, json={"accessToken": "token-2", "expireIn": 7200}),
    ])
    member_route = respx.post(f"{OAPI}/topapi/v2/user/get").mock(side_effect=[
        httpx.Response(503, json={"errcode": -1}),
        httpx.Response(200, json={"errcode": 40014, "errmsg": "invalid"}),
        httpx.Response(200, json={"errcode": 40014, "errmsg": "invalid"}),
        httpx.Response(200, json={"errcode": 0, "result": {}}),
    ])

    with pytest.raises(DingTalkProviderError, match="40014"):
        await _client(sleep=lambda _: None).get_member("employee-1")

    assert member_route.call_count == 3


@pytest.mark.asyncio
async def test_all_httpx_request_errors_are_redacted_without_exception_chaining() -> None:
    secret = "query-secret-token"

    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ProxyError(
            f"proxy failed for {request.url}", request=request
        )

    transport = httpx.MockTransport(fail)
    injected = httpx.AsyncClient(transport=transport)
    client = _client(http_client=injected)
    object.__setattr__(client, "_token", secret)
    object.__setattr__(client, "_token_expires_at", float("inf"))
    try:
        with pytest.raises(DingTalkProviderError) as caught:
            await client.get_member("employee-sensitive")
        stable_rendering = str(caught.value) + repr(caught.value)
        traceback_rendering = "".join(
            traceback.format_exception(caught.value)
        )
        assert caught.value.__cause__ is None
        assert caught.value.__suppress_context__ is True
        assert secret not in traceback_rendering
        assert "employee-sensitive" not in stable_rendering
    finally:
        await injected.aclose()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
async def test_http_client_logs_redact_query_and_header_tokens(caplog, level) -> None:
    secrets = ("legacy-query-secret", "user-header-secret")

    def log_wire_request(request: httpx.Request) -> httpx.Response:
        logging.getLogger("httpcore.http11").log(
            level, "wire request=%r", request
        )
        if request.url.path.endswith("/users/me"):
            return httpx.Response(400, json={"code": "denied"})
        return httpx.Response(200, json={"errcode": 1, "errmsg": "no"})

    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(
        return_value=httpx.Response(
            200, json={"accessToken": secrets[0], "expireIn": 7200}
        )
    )
    respx.post(f"{OAPI}/topapi/v2/user/get").mock(
        side_effect=log_wire_request
    )
    respx.post(f"{API}/v1.0/oauth2/userAccessToken").mock(
        return_value=httpx.Response(200, json={
            "accessToken": secrets[1], "expireIn": 7200, "corpId": "test-corp",
        })
    )
    respx.get(f"{API}/v1.0/contact/users/me").mock(
        side_effect=log_wire_request
    )
    caplog.set_level(level)

    with pytest.raises(DingTalkProviderError):
        await _client().get_member("employee-1")
    with pytest.raises(DingTalkProviderError):
        await _client(flow="qr").exchange_login_code("one-time-code")

    assert all(secret not in caplog.text for secret in secrets)


def test_self_owned_http_client_ignores_proxy_environment() -> None:
    client = _client()
    assert client._client._trust_env is False
