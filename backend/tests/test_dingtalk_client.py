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


def _all_request_error_types(error_type):
    return tuple(
        child
        for direct in error_type.__subclasses__()
        for child in (direct, *_all_request_error_types(direct))
    )


REQUEST_ERROR_TYPES = _all_request_error_types(httpx.RequestError)


def _assert_no_provider_material_in_exception_tree(
    error: BaseException,
    secrets: tuple[str, ...],
) -> None:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        rendered = "".join(traceback.format_exception(current))
        assert all(secret not in rendered for secret in secrets)
        for name in ("request", "response", "content", "url", "body"):
            assert not hasattr(current, name)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    assert error.__cause__ is None
    assert error.__context__ is None


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
async def test_qr_oauth_exchange_binds_high_entropy_pkce_verifier() -> None:
    token_route = respx.post(f"{API}/v1.0/oauth2/userAccessToken").mock(
        return_value=httpx.Response(200, json={
            "accessToken": "user-token", "expireIn": 7200, "corpId": "test-corp",
        })
    )
    respx.get(f"{API}/v1.0/contact/users/me").mock(
        return_value=httpx.Response(200, json={"unionId": "union-1"})
    )
    verifier = "v" * 43

    await _client(flow="qr").exchange_login_code("one-time-code", verifier)

    assert json.loads(token_route.calls[0].request.content)["codeVerifier"] == verifier


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
@pytest.mark.parametrize(
    ("extension", "gender", "status"),
    [
        ({"性别": "男"}, "male", "valid"),
        ({"性别": " 女 "}, "female", "valid"),
        ({}, None, "missing"),
        ({"性别": ""}, None, "invalid"),
        ({"性别": "未知"}, None, "invalid"),
        ({"性别": ["男"]}, None, "invalid"),
    ],
)
@respx.mock
async def test_member_normalizes_only_the_configured_gender_attribute(
    extension, gender, status
) -> None:
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    respx.post(f"{OAPI}/topapi/v2/user/get").mock(
        return_value=httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "result": {
            "userid": "employee-1",
            "unionid": "union-1",
            "name": "Employee",
            "active": True,
            "dept_id_list": [1],
            "extension": extension,
        }})
    )
    client = _client()
    member = await client.get_member("employee-1")
    assert member.gender == gender
    assert member.gender_attribute_status == status
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "extension, status",
    [(pytest.param(None, "missing", id="absent")), ([], "invalid")],
)
async def test_member_gender_attribute_requires_an_object_extension(
    extension, status
) -> None:
    result = {
        "userid": "employee-1",
        "unionid": "union-1",
        "name": "Employee",
        "active": True,
        "dept_id_list": [1],
    }
    if extension is not None:
        result["extension"] = extension
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    respx.post(f"{OAPI}/topapi/v2/user/get").mock(
        return_value=httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "result": result})
    )

    member = await _client().get_member("employee-1")

    assert member.gender is None
    assert member.gender_attribute_status == status


@pytest.mark.asyncio
@respx.mock
async def test_member_list_normalizes_gender_without_retaining_extension() -> None:
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    respx.post(f"{OAPI}/topapi/v2/user/list").mock(
        return_value=httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "result": {
            "has_more": False,
            "next_cursor": 0,
            "list": [{
                "userid": "employee-1",
                "unionid": "union-1",
                "name": "Employee",
                "active": True,
                "dept_id_list": [1],
                "extension": {"性别": "女", "other": "discarded"},
            }],
        }})
    )

    members = [member async for member in _client().iter_department_members(1)]

    assert members[0].gender == "female"
    assert members[0].gender_attribute_status == "valid"
    assert not hasattr(members[0], "extension")


@pytest.mark.asyncio
@respx.mock
async def test_invalid_member_gender_extension_never_appears_in_errors_or_logs(caplog) -> None:
    extension = {"性别": "男", "other": "女"}
    private_values = (
        "男",
        "女",
        "Employee Sensitive",
        "employee-sensitive",
        "union-sensitive",
    )
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    respx.post(f"{OAPI}/topapi/v2/user/get").mock(
        return_value=httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "result": {
            "userid": "employee-sensitive",
            "unionid": "union-sensitive",
            "name": "Employee Sensitive",
            "active": True,
            "dept_id_list": [1],
            "extension": extension,
        }})
    )
    caplog.set_level(logging.WARNING)

    with pytest.raises(DingTalkProviderError) as caught:
        await _client().get_member("different-employee")

    rendered = str(caught.value) + repr(caught.value) + caplog.text
    assert all(value not in rendered for value in private_values)
    assert repr(extension) not in rendered


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
async def test_directory_member_empty_continuation_page_is_rejected() -> None:
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    respx.post(f"{OAPI}/topapi/v2/user/list").mock(
        return_value=httpx.Response(200, json={
            "errcode": 0,
            "result": {"has_more": True, "next_cursor": 100, "list": []},
        })
    )
    client = _client()

    with pytest.raises(DingTalkProviderError) as caught:
        _ = [item async for item in client.iter_department_members(2)]
    assert caught.value.error_code == "pagination_empty_page"


@pytest.mark.asyncio
@respx.mock
async def test_directory_member_terminal_page_may_omit_next_cursor() -> None:
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(return_value=_token())
    respx.post(f"{OAPI}/topapi/v2/user/list").mock(
        return_value=httpx.Response(200, json={
            "errcode": 0,
            "result": {"has_more": False, "list": []},
        })
    )
    client = _client()

    assert [item async for item in client.iter_department_members(1)] == []


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
@pytest.mark.parametrize("error_type", REQUEST_ERROR_TYPES)
async def test_all_httpx_request_errors_have_no_raw_context_or_provider_material(
    error_type,
) -> None:
    secrets = (
        "query-secret-token",
        "test-app-secret",
        "single-use-login-code",
        "employee-sensitive",
    )

    async def fail(request: httpx.Request) -> httpx.Response:
        raise error_type(
            "provider failed " + " ".join(secrets) +
            f" url={request.url} content={request.content!r}",
            request=request,
        )

    transport = httpx.MockTransport(fail)
    injected = httpx.AsyncClient(transport=transport)
    client = _client(http_client=injected)
    object.__setattr__(client, "_token", secrets[0])
    object.__setattr__(client, "_token_expires_at", float("inf"))
    try:
        with pytest.raises(DingTalkProviderError) as caught:
            await client.get_member(secrets[3])
        stable_rendering = str(caught.value) + repr(caught.value)
        traceback_rendering = "".join(
            traceback.format_exception(caught.value)
        )
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert not hasattr(caught.value, "request")
        assert not hasattr(caught.value, "response")
        assert not hasattr(caught.value, "content")
        assert not hasattr(caught.value, "url")
        assert all(secret not in traceback_rendering for secret in secrets)
        assert secrets[3] not in stable_rendering
    finally:
        await injected.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    (
        "raw_json",
        "application_token",
        "legacy_envelope",
        "in_client_login",
        "qr_token",
        "qr_profile",
        "member",
        "union",
        "department_item",
        "member_page",
        "member_page_entry",
        "provider_error_body",
    ),
)
async def test_all_provider_parse_failures_discard_raw_exception_chains(
    scenario: str,
) -> None:
    secrets = (
        "raw-response-secret",
        "provider-token-secret",
        "single-use-code-secret",
        "employee-secret",
        "union-secret",
    )

    def response_for(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if scenario == "raw_json":
            return httpx.Response(
                200,
                content=b'{"raw-response-secret": [',
                request=request,
            )
        if path.endswith("/oauth2/accessToken"):
            if scenario == "application_token":
                return httpx.Response(
                    200,
                    json={"accessToken": [secrets[0]], "expireIn": 7200},
                    request=request,
                )
            return httpx.Response(
                200,
                json={"accessToken": secrets[1], "expireIn": 7200},
                request=request,
            )
        if path.endswith("/oauth2/userAccessToken"):
            payload = (
                {"accessToken": [secrets[0]], "corpId": "test-corp"}
                if scenario == "qr_token"
                else {"accessToken": secrets[1], "corpId": "test-corp"}
            )
            return httpx.Response(200, json=payload, request=request)
        if path.endswith("/contact/users/me"):
            return httpx.Response(
                200,
                json={"unionId": [secrets[0]]},
                request=request,
            )
        if path.endswith("/getuserinfo"):
            return httpx.Response(
                200,
                json={"errcode": 0, "result": [secrets[0]]},
                request=request,
            )
        if scenario == "legacy_envelope":
            return httpx.Response(
                200,
                json={"errcode": [[secrets[0]]], "errmsg": secrets[0]},
                request=request,
            )
        if scenario == "provider_error_body":
            return httpx.Response(
                400,
                json={"code": [[secrets[0]]], "message": secrets[0]},
                request=request,
            )
        if path.endswith("/getbyunionid"):
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "result": {"contact_type": [secrets[0]], "userid": secrets[3]},
                },
                request=request,
            )
        if path.endswith("/department/listsub"):
            return httpx.Response(
                200,
                json={"errcode": 0, "result": [[secrets[0]]]},
                request=request,
            )
        if path.endswith("/user/list"):
            entry = [secrets[0]] if scenario == "member_page_entry" else {}
            payload = (
                {"has_more": [secrets[0]], "next_cursor": 0, "list": []}
                if scenario == "member_page"
                else {"has_more": False, "next_cursor": 0, "list": [entry]}
            )
            return httpx.Response(
                200, json={"errcode": 0, "result": payload}, request=request
            )
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                "result": {
                    "userid": [secrets[0]],
                    "unionid": secrets[4],
                    "name": secrets[0],
                    "active": True,
                    "dept_id_list": [1],
                },
            },
            request=request,
        )

    injected = httpx.AsyncClient(transport=httpx.MockTransport(response_for))
    flow = "qr" if scenario in {"qr_token", "qr_profile"} else "in_client"
    client = _client(flow=flow, http_client=injected)
    if scenario not in {"application_token"}:
        object.__setattr__(client, "_token", secrets[1])
        object.__setattr__(client, "_token_expires_at", float("inf"))
    try:
        with pytest.raises(DingTalkProviderError) as caught:
            if scenario in {"in_client_login", "qr_token", "qr_profile"}:
                await client.exchange_login_code(secrets[2])
            elif scenario == "union":
                await client.resolve_union_member(secrets[4])
            elif scenario == "department_item":
                await anext(client.iter_departments())
            elif scenario in {"member_page", "member_page_entry"}:
                await anext(client.iter_department_members(1))
            else:
                await client.get_member(secrets[3])
        _assert_no_provider_material_in_exception_tree(caught.value, secrets)
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
