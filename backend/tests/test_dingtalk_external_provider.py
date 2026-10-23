from __future__ import annotations

import hashlib
import importlib
import json
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.datastructures import QueryParams

from app.control_plane.partner_models import (
    PartnerIdentityError,
    VerifiedProviderSubject,
)

NOW = datetime(2026, 10, 23, 8, tzinfo=UTC)
REDIRECT = "https://agent.orbbec.com.cn/api/v1/auth/dingtalk/callback"
SNS_URL = "https://oapi.dingtalk.com/sns/getuserinfo_bycode"
CLIENT_ID = "ding-external-app"
SECRET = "secret-never-output"
TOKEN = "token-never-output"
UNION_ID = "union-never-output"
NICK = "个人用户"
SUBJECT = hashlib.sha256(CLIENT_ID.encode()).hexdigest() + ":" + UNION_ID
CALLBACK = {"code": "auth-code-never-output", "state": "fae-dt-sns-v1.state"}


def provider_class():
    try:
        module = importlib.import_module("app.control_plane.dingtalk_external_provider")
    except ModuleNotFoundError:
        pytest.fail("external personal DingTalk provider is not implemented")
    return module.DingTalkExternalIdentityProvider


def provider(*, handler=None, **kwargs):
    options = {
        "client_id": CLIENT_ID,
        "client_secret": SECRET,
        "redirect_uri": REDIRECT,
        "verified_at_for_subject": lambda _subject: None,
        "now": lambda: NOW,
        "transport": httpx.MockTransport(handler or success),
    }
    options.update(kwargs)
    return provider_class()(**options)


def success(request):
    assert request.url.copy_with(query=None) == httpx.URL(SNS_URL)
    return httpx.Response(200, json={"errcode": 0, "user_info": {"unionid": UNION_ID, "nick": NICK}})


@pytest.mark.asyncio
async def test_personal_authorization_requests_only_sns_login_without_corpid():
    instance = provider()
    try:
        url = urlsplit(instance.begin_auth(CALLBACK["state"]))
        assert (url.scheme, url.netloc, url.path) == (
            "https", "oapi.dingtalk.com", "/connect/qrconnect",
        )
        assert parse_qs(url.query) == {
            "appid": [CLIENT_ID], "redirect_uri": [REDIRECT],
            "response_type": ["code"], "scope": ["snsapi_login"],
            "state": [CALLBACK["state"]],
        }
        assert "corpid" not in url.query.lower()
    finally:
        await instance.aclose()


@pytest.mark.asyncio
async def test_callback_uses_one_signed_sns_exchange_with_independent_openssl_vector():
    requests = []
    def handler(request):
        requests.append(request)
        return success(request)
    # Expected literal independently calculated with OpenSSL, not implementation helpers.
    instant = datetime.fromtimestamp(1546084445.901, UTC)
    instance = provider(handler=handler, client_secret="testappSecret", now=lambda: instant)
    try:
        verified = await instance.finish_auth(CALLBACK)
        assert verified == VerifiedProviderSubject(
            provider_kind="dingtalk_external", provider_subject=SUBJECT,
            verified_at=instant, display_name=NICK,
        )
        assert len(requests) == 1
        request = requests[0]
        assert request.method == "POST"
        assert request.url.copy_with(query=None) == httpx.URL(SNS_URL)
        assert dict(request.url.params) == {
            "accessKey": CLIENT_ID, "timestamp": "1546084445901",
            "signature": "HCbG3xNE3vzhO+u7qCUL1jS5hsu2n5r2cFhnTrtyDAE=",
        }
        assert "HCbG3xNE3vzhO%2Bu7qCUL1jS5hsu2n5r2cFhnTrtyDAE%3D" in str(request.url)
        assert "%252B" not in str(request.url)
        assert json.loads(request.content) == {"tmp_auth_code": CALLBACK["code"]}
        assert "x-acs-dingtalk-access-token" not in request.headers
        assert await instance.check_subject(SUBJECT) == "active"
        for sensitive in (SECRET, "testappSecret", TOKEN, UNION_ID, NICK, CALLBACK["code"]):
            assert sensitive not in repr(instance)
            assert sensitive not in repr(verified)
    finally:
        await instance.aclose()


@pytest.mark.parametrize(
    "redirect",
    [
        "http://agent.orbbec.com.cn/api/v1/auth/dingtalk/callback",
        "https://evil.test/api/v1/auth/dingtalk/callback",
        "https://agent.orbbec.com.cn.evil.test/api/v1/auth/dingtalk/callback",
        REDIRECT + "?next=https://evil.test",
        REDIRECT + "#fragment",
        REDIRECT + "/",
        REDIRECT.replace("/api/", "/%61pi/"),
        REDIRECT.replace("https://", "https://user@"),
        REDIRECT.replace(".cn/", ".cn:444/"),
    ],
)
def test_redirect_uri_requires_exact_https_allowlist(redirect):
    with pytest.raises(ValueError, match="^dingtalk_external_configuration_invalid$"):
        provider(redirect_uri=redirect)


@pytest.mark.parametrize("field", ["client_id", "client_secret"])
@pytest.mark.parametrize("value", [None, "", " padded ", "bad\nvalue", "x" * 4097])
def test_invalid_configuration_is_redacted(field, value):
    with pytest.raises(ValueError, match="^dingtalk_external_configuration_invalid$"):
        provider(**{field: value})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callback",
    [
        {}, {"state": "ok"}, {"code": "ok"},
        {"code": "", "state": "ok"}, {"code": " padded ", "state": "ok"},
        {"code": "bad\ncode", "state": "ok"}, {"code": 123, "state": "ok"},
        {"code": "x" * 4097, "state": "ok"}, {"code": "ok", "state": ""},
        {"code": "ok", "state": ["bad"]}, {"authCode": "ok", "state": "ok"},
        {"code": "ok", "state": "ok", "authCode": "other"},
        {"code": "ok", "state": "ok", "authCode": "ok"},
        {"code": "ok", "state": "ok", "error": "provider raw error"},
        {"code": "ok", "state": "ok", "error_description": "raw"},
        {"code": "ok", "state": "ok", "unknown": "raw"},
        QueryParams("code=one&code=two&state=ok"),
        QueryParams("code=one&state=ok&state=other"),
        QueryParams("code=one&state=ok&authCode=one&authCode=one"),
    ],
)
async def test_invalid_or_conflicting_callback_fails_before_http(callback):
    requests = []
    instance = provider(handler=lambda request: requests.append(request))
    try:
        with pytest.raises(PartnerIdentityError) as failure:
            await instance.finish_auth(callback)
        assert failure.value.code == "partner_auth_invalid"
        assert requests == []
    finally:
        await instance.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", [None, "", " padded ", "bad\nvalue", 5, [], "x" * 4097]
)
async def test_user_info_must_be_an_object(value):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"errcode": 0, "user_info": value})

    instance = provider(handler=handler)
    try:
        with pytest.raises(
            PartnerIdentityError, match="^partner_identity_unavailable$"
        ):
            await instance.finish_auth(CALLBACK)
        assert len(requests) == 1
        assert await instance.check_subject(SUBJECT) == "inactive"
    finally:
        await instance.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("unionid", None),
        ("unionid", ""),
        ("unionid", 10),
        ("unionid", " padded "),
        ("unionid", "bad\nvalue"),
        ("unionid", "x" * 513),
        ("unionid", "中文"),
        ("nick", None),
        ("nick", ""),
        ("nick", 10),
        ("nick", " padded "),
        ("nick", "bad\nvalue"),
        ("nick", "bad\x00value"),
        ("nick", "bad\u200bvalue"),
        ("nick", "x" * 257),
    ],
)
async def test_profile_requires_strict_unionid_and_nickname(field, value):
    def handler(request):
        payload = {"unionid": UNION_ID, "nick": NICK, field: value}
        return httpx.Response(200, json={"errcode": 0, "user_info": payload})

    instance = provider(handler=handler)
    try:
        with pytest.raises(
            PartnerIdentityError, match="^partner_identity_unavailable$"
        ):
            await instance.finish_auth(CALLBACK)
        assert await instance.check_subject(SUBJECT) == "inactive"
    finally:
        await instance.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_mode",
    ["http", "provider", "timeout", "invalid_json", "non_object", "redirect"],
)
async def test_provider_failures_are_redacted_and_never_retried(failure_mode):
    requests = []
    raw = " ".join((SECRET, TOKEN, UNION_ID, NICK, CALLBACK["code"]))

    def handler(request):
        requests.append(request)
        if failure_mode == "timeout":
            raise httpx.ReadTimeout(raw, request=request)
        if failure_mode == "invalid_json":
            return httpx.Response(200, content=raw.encode())
        if failure_mode == "non_object":
            return httpx.Response(200, json=[raw])
        if failure_mode == "redirect":
            return httpx.Response(
                302, headers={"location": "https://evil.test/" + TOKEN}
            )
        if failure_mode == "provider":
            return httpx.Response(
                200,
                json={
                    "code": raw,
                    "message": raw,
                    "accessToken": TOKEN,
                    "unionid": UNION_ID,
                    "nick": NICK,
                },
            )
        return httpx.Response(403, json={"message": raw})

    instance = provider(handler=handler)
    try:
        with pytest.raises(PartnerIdentityError) as failure:
            await instance.finish_auth(CALLBACK)
        assert failure.value.code == "partner_identity_unavailable"
        assert len(requests) == 1
        assert failure.value.__suppress_context__
        for sensitive in (SECRET, TOKEN, UNION_ID, NICK, CALLBACK["code"]):
            assert sensitive not in str(failure.value)
            assert sensitive not in repr(failure.value)
    finally:
        await instance.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verified_at,expected",
    [
        (None, "inactive"),
        (NOW, "active"),
        (NOW - timedelta(hours=8, microseconds=-1), "active"),
        (NOW - timedelta(hours=8), "inactive"),
        (NOW - timedelta(hours=9), "inactive"),
        (NOW + timedelta(microseconds=1), "inactive"),
        (NOW.replace(tzinfo=None), "inactive"),
        ("malformed", "inactive"),
    ],
)
async def test_persisted_oauth_lease_requires_recent_aware_nonfuture_proof(
    verified_at, expected
):
    seen = []
    instance = provider(
        verified_at_for_subject=lambda subject: seen.append(subject) or verified_at
    )
    try:
        assert await instance.check_subject(SUBJECT) == expected
        assert seen == [SUBJECT]
    finally:
        await instance.aclose()


@pytest.mark.asyncio
async def test_callback_proof_expires_at_sixty_seconds_and_never_accepts_future_proof():
    current = [NOW]
    instance = provider(now=lambda: current[0])
    try:
        await instance.finish_auth(CALLBACK)
        current[0] = NOW + timedelta(seconds=59, microseconds=999999)
        assert await instance.check_subject(SUBJECT) == "active"
        current[0] = NOW + timedelta(seconds=60)
        assert await instance.check_subject(SUBJECT) == "inactive"
        current[0] = NOW
        await instance.finish_auth(CALLBACK)
        current[0] = NOW - timedelta(seconds=1)
        assert await instance.check_subject(SUBJECT) == "inactive"
    finally:
        await instance.aclose()


@pytest.mark.asyncio
async def test_subjects_are_namespaced_by_client_and_invalid_subjects_do_not_query_storage():
    instance = provider(
        client_id="different-app",
        verified_at_for_subject=lambda _subject: pytest.fail("foreign subject queried"),
    )
    try:
        verified = await instance.finish_auth(CALLBACK)
        assert (
            verified.provider_subject
            == hashlib.sha256(b"different-app").hexdigest() + ":" + UNION_ID
        )
        assert await instance.check_subject(SUBJECT) == "inactive"
        for malformed in (
            "",
            None,
            "raw-union",
            hashlib.sha256(b"different-app").hexdigest() + ":",
        ):
            assert await instance.check_subject(malformed) == "inactive"
    finally:
        await instance.aclose()


@pytest.mark.asyncio
async def test_callback_proofs_are_bounded_and_expired_entries_are_evicted():
    current = [NOW]
    counter = [0]

    def handler(request):
        counter[0] += 1
        return httpx.Response(
            200, json={"errcode": 0, "user_info": {"unionid": f"union-{counter[0]}", "nick": NICK}}
        )

    instance = provider(handler=handler, now=lambda: current[0])
    subjects = []
    try:
        for _ in range(1025):
            subjects.append((await instance.finish_auth(CALLBACK)).provider_subject)
        assert await instance.check_subject(subjects[0]) == "inactive"
        assert await instance.check_subject(subjects[1]) == "active"
        assert await instance.check_subject(subjects[-1]) == "active"
        current[0] += timedelta(seconds=60)
        assert await instance.check_subject(subjects[-1]) == "inactive"
        # No tokens/codes or nicknames are retained as callback proof state.
        assert len(instance._callback_proofs) == 0
    finally:
        await instance.aclose()


@pytest.mark.asyncio
async def test_persistence_failure_crosses_redacted_boundary():
    def unavailable(_subject):
        raise RuntimeError(SECRET + UNION_ID)

    instance = provider(verified_at_for_subject=unavailable)
    try:
        with pytest.raises(
            PartnerIdentityError, match="^partner_identity_unavailable$"
        ) as failure:
            await instance.check_subject(SUBJECT)
        assert failure.value.__suppress_context__
    finally:
        await instance.aclose()


@pytest.mark.asyncio
async def test_aclose_closes_owned_http_client_and_clears_callback_proof():
    class Transport(httpx.AsyncBaseTransport):
        closed = False

        async def handle_async_request(self, request):
            return success(request)

        async def aclose(self):
            self.closed = True

    transport = Transport()
    instance = provider(transport=transport)
    await instance.finish_auth(CALLBACK)
    await instance.aclose()
    assert transport.closed
    assert instance._callback_proofs == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("errcode", [None, False, True, "0", 0.0, [], {}, 40078])
async def test_errcode_requires_exact_integer_zero(errcode):
    instance = provider(handler=lambda _: httpx.Response(200, json={
        "errcode": errcode, "user_info": {"unionid": UNION_ID, "nick": NICK},
    }))
    try:
        with pytest.raises(PartnerIdentityError, match="^partner_identity_unavailable$"):
            await instance.finish_auth(CALLBACK)
        assert await instance.check_subject(SUBJECT) == "inactive"
    finally:
        await instance.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("clock", [lambda: None, lambda: NOW.replace(tzinfo=None), lambda: "bad"])
async def test_invalid_clock_fails_before_signed_http(clock):
    calls = []
    instance = provider(now=clock, handler=lambda request: calls.append(request))
    try:
        with pytest.raises(PartnerIdentityError, match="^partner_identity_unavailable$"):
            await instance.finish_auth(CALLBACK)
        assert calls == []
    finally:
        await instance.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
async def test_http_logs_redact_signed_query_and_telemetry_uses_only_safe_fields(caplog, level):
    requests = []
    def handler(request):
        requests.append(request)
        # Match httpcore's debug request-target representation too.
        logging.getLogger("httpcore.http11").debug("request target %s", request.url.raw_path)
        return httpx.Response(200, json={"errcode": 40078, "errmsg": SECRET + UNION_ID + CALLBACK["code"]})
    instance = provider(handler=handler)
    try:
        with caplog.at_level(level):
            with pytest.raises(PartnerIdentityError):
                await instance.finish_auth(CALLBACK)
        signature = requests[0].url.params["signature"]
        from urllib.parse import quote
        for secret in (signature, quote(signature, safe=""), SECRET, UNION_ID, NICK, CALLBACK["code"]):
            assert secret not in caplog.text
        assert "signature=<redacted>" in caplog.text
        failures = [r for r in caplog.records if r.name == "app.control_plane.dingtalk_external_provider"]
        assert len(failures) == 1
        assert failures[0].getMessage() == "dingtalk_external_exchange_failed reason=provider_error http_status=200 provider_errcode=40078"
    finally:
        await instance.aclose()
