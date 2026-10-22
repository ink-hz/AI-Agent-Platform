"""Offline contract tests using real reader and snapshot core, MockTransport only."""
import asyncio
import socket
import traceback
from pathlib import Path

import httpx
import pytest

from app.sdk_workbench.story_snapshot import SnapshotScope, collect_story_snapshot
from app.sdk_workbench.readonly_story_reader import ReadOnlyStoryReader
from app.sdk_workbench.zentao import (ZenTaoAuthenticationError, ZenTaoProtocolError,
    ZenTaoRejected, ZenTaoRequestRejected, ZenTaoUnavailable)

ORIGIN = "https://readonly-zentao.invalid"
TOKEN = "fixture-secret-token"
SCOPE = SnapshotScope("fixture", "22.6-declared", 1, "service", "declared")
ACTOR = object()
PARAMS = {"browseType": "allstory", "recPerPage": 50, "pageID": 1}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("real network forbidden")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def reader_type():
    return ReadOnlyStoryReader

def make(cls, handler):
    return cls(origin=ORIGIN, token=TOKEN, scope=SCOPE, actor=ACTOR,
               transport=httpx.MockTransport(handler))


async def send(reader, **overrides):
    args = dict(actor=ACTOR, method="GET", path="products/1/stories", params=PARAMS)
    args.update(overrides)
    return await reader.request(**args)


@pytest.mark.parametrize("changes", [
    {"actor": object()}, {"actor": None}, {"method": "POST"}, {"method": "get"},
    *[{"api_version": x} for x in (True, 2.0, 1)],
    *[{"path": x} for x in ("products/2/stories", ORIGIN + "/products/1/stories",
        "products/%31/stories", "products/1/stories?x=1", "products/1/stories#x",
        "/products/1/stories", "products/1/../1/stories")],
    {"payload": {}}, {"params": None}, {"params": {}},
    {"params": {**PARAMS, "extra": 1}}, {"params": {**PARAMS, "browseType": "assignedTo"}},
    *[{"params": {**PARAMS, "recPerPage": x}} for x in (True, 50.0, "50", 49)],
    *[{"params": {**PARAMS, "pageID": x}} for x in (True, 1.0, "1", 0, 21)],
])
def test_scope_rejection_performs_no_http(reader_type, changes):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"status": "success"})
    async def run():
        reader = make(reader_type, handler)
        try:
            with pytest.raises(ValueError):
                await send(reader, **changes)
        finally:
            await reader.close()
    asyncio.run(run())
    assert calls == []


@pytest.mark.parametrize("origin", ["", "ftp://host", "https://user:secret@host", "https://host/a",
    "https://host?secret=x", "https://host#secret", "https://host:", "https://host:0",
    "https://host:65536", "https://", "https://bad host", "https://host\n", "//host"])
def test_invalid_origin(reader_type, origin):
    with pytest.raises(ValueError, match="readonly_origin_invalid"):
        args = dict(origin=origin, token=TOKEN, scope=SCOPE, actor=ACTOR,
                    transport=httpx.MockTransport(lambda req: pytest.fail("HTTP forbidden")))
        reader_type(**args)


@pytest.mark.parametrize("token", [None, "", "\nsecret", "secret\r", "secret\x00", "secret\x7f"])
def test_invalid_token_is_sanitized(reader_type, token):
    with pytest.raises(ValueError, match="readonly_token_invalid"):
        reader_type(origin=ORIGIN, token=token, scope=SCOPE, actor=ACTOR)


def test_requires_scope_actor(reader_type):
    for scope, actor in ((None, ACTOR), (object(), ACTOR), (SCOPE, None)):
        with pytest.raises(ValueError):
            reader_type(origin=ORIGIN, token=TOKEN, scope=scope, actor=actor)


@pytest.mark.parametrize("origin", [ORIGIN, ORIGIN + "/", "http://127.0.0.1:8080", "https://[::1]:8443"])
def test_exact_get_cookie_token_timeout_close_and_no_file_reads(reader_type, monkeypatch, origin):
    calls = []
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:9999")
    def forbidden(*args, **kwargs):
        raise AssertionError("credentials must be injected")
    monkeypatch.setattr(Path, "read_text", forbidden)
    def handler(req):
        calls.append(req)
        assert req.method == "GET" and req.content == b""
        assert str(req.url).startswith(origin.rstrip("/") + "/api.php/v2/products/1/stories?")
        assert dict(req.url.params) == {"browseType": "allstory", "recPerPage": "50", "pageID": str(len(calls))}
        assert req.headers["Token"] == TOKEN and "cookie" not in req.headers
        assert req.extensions["timeout"] == dict(connect=10.0, read=10.0, write=10.0, pool=10.0)
        return httpx.Response(200, json={"status": "success", "native": "保留"},
                              headers={"Set-Cookie": "session=private; Path=/"})
    async def run():
        reader = reader_type(origin=origin, token=TOKEN, scope=SCOPE, actor=ACTOR,
                             transport=httpx.MockTransport(handler))
        assert reader.scope is SCOPE
        with pytest.raises(AttributeError):
            reader.scope = SnapshotScope("other", "22.6", 2, "service", "declared")
        assert TOKEN not in repr(reader) and origin not in repr(reader)
        try:
            assert (await send(reader))["native"] == "保留"
            await send(reader, params={**PARAMS, "pageID": 2})
        finally:
            await reader.close()
        await reader.close()
        with pytest.raises(ValueError, match="readonly_reader_closed"):
            await send(reader)
    asyncio.run(run())
    assert len(calls) == 2


@pytest.mark.parametrize("status,exception", [
    (401, ZenTaoAuthenticationError), *[(s, ZenTaoRequestRejected) for s in (400, 403, 404, 409, 422)],
    *[(s, ZenTaoUnavailable) for s in (429, 500, 503, 599)],
    *[(s, ZenTaoProtocolError) for s in (201, 204, 301, 302, 307, 308, 418)],
])
def test_http_error_no_retry_redirect_or_secret_chain(reader_type, status, exception, caplog):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, text=TOKEN + ORIGIN,
            headers={"Location": "https://redirect-secret.invalid", "Set-Cookie": "secret=x"})
    async def run():
        reader = make(reader_type, handler)
        try:
            with pytest.raises(exception) as caught:
                await send(reader)
            rendered = "".join(traceback.format_exception(caught.value))
            assert TOKEN not in rendered and ORIGIN not in rendered and "redirect-secret" not in rendered
        finally:
            await reader.close()
    asyncio.run(run())
    assert len(calls) == 1 and TOKEN not in caplog.text


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError])
def test_transport_failure_is_sanitized_single_attempt(reader_type, error_type):
    calls = []
    def handler(req):
        calls.append(req)
        raise error_type(TOKEN + ORIGIN, request=req)
    async def run():
        reader = make(reader_type, handler)
        try:
            with pytest.raises(ZenTaoUnavailable) as caught:
                await send(reader)
            assert TOKEN not in "".join(traceback.format_exception(caught.value))
        finally:
            await reader.close()
    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize("body,exception", [
    (b"not json", ZenTaoProtocolError), (b'\xff', ZenTaoProtocolError),
    ('{"status":"success"}'.encode("utf-16"), ZenTaoProtocolError),
    (b'[]', ZenTaoProtocolError), (b'{}', ZenTaoProtocolError), (b'{"status":true}', ZenTaoProtocolError),
    *[(b'{"status":"success","n":' + x + b'}', ZenTaoProtocolError) for x in (b'NaN', b'Infinity', b'-Infinity')],
    (b'{"status":"success","status":"success"}', ZenTaoProtocolError),
    (b'{"status":"success","nested":{"x":1,"x":2}}', ZenTaoProtocolError),
    (b'{"status":"fail","message":"fixture-secret-token"}', ZenTaoRejected),
    (b'{"status":"success","n":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}', ZenTaoProtocolError),
])
def test_strict_utf8_json_envelope(reader_type, body, exception):
    async def run():
        reader = make(reader_type, lambda req: httpx.Response(200, content=body))
        try:
            with pytest.raises(exception) as caught:
                await send(reader)
            assert TOKEN not in "".join(traceback.format_exception(caught.value))
        finally:
            await reader.close()
    asyncio.run(run())


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.read_count, self.closed = chunks, 0, False
    async def __aiter__(self):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk
    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("excess", [False, True])
def test_decoded_stream_limit_and_response_closure(reader_type, excess):
    import gzip
    body = b'{"status":"success","pad":"' + b'x' * (1048576 - 29) + b'"}'
    assert len(body) == 1048576
    if excess:
        body += b' '
    stream = Chunks([gzip.compress(body)])
    async def run():
        reader = make(reader_type, lambda req: httpx.Response(200, stream=stream,
            headers={"Content-Encoding": "gzip"}))
        try:
            if excess:
                with pytest.raises(ZenTaoProtocolError, match="readonly_response_too_large"):
                    await send(reader)
            else:
                assert (await send(reader))["status"] == "success"
        finally:
            await reader.close()
    asyncio.run(run())
    assert stream.closed


def page(items, total=2):
    return {"status": "success", "stories": items, "pager": {"pageTotal": 2, "recTotal": total}}


@pytest.mark.parametrize("second,code", [
    (page([{"id": 2, "product": 1}]), None), (401, "authentication_failed"),
    (b'{broken', "source_protocol_error"),
    (page([{"id": 2, "product": 2}]), "story_product_mismatch"),
    (page([{"id": 2, "product": 1}], total=3), "pager_drift"),
])
def test_reader_with_original_snapshot_two_page_evidence(reader_type, second, code):
    first = page([{"id": "01", "product": 1, "title": "原始值"}])
    calls = []
    def handler(req):
        calls.append(req)
        assert req.headers["Token"] == TOKEN and "cookie" not in req.headers
        assert req.url.params["pageID"] == str(len(calls))
        result = first if len(calls) == 1 else second
        if isinstance(result, int):
            return httpx.Response(result, text=TOKEN)
        if isinstance(result, bytes):
            return httpx.Response(200, content=result)
        return httpx.Response(200, json=result)
    async def run():
        reader = make(reader_type, handler)
        try:
            return await collect_story_snapshot(reader, ACTOR, scope=reader.scope)
        finally:
            await reader.close()
    report = asyncio.run(run())
    assert len(calls) == 2 and report["pages"][0]["raw_response"] == first
    assert report["scope_verification"] == "caller_declared_not_verified"
    assert report["boundaries"]["production_import_eligible"] is False
    if code is None:
        assert report["outcome"] == "complete" and report["last_valid_page"] == 2
        assert report["stories"] == first["stories"] + second["stories"]
    else:
        assert report["outcome"] == "partial" and report["last_valid_page"] == 1
        assert report["stopped_page"] == 2 and report["stories"] == first["stories"]
        assert report["issues"][0]["code"] == code
        assert report["pages"][1]["raw_response"] == (second if isinstance(second, dict) else None)
    assert TOKEN not in repr(report)


def test_invalid_encoded_response_is_sanitized_and_closed(reader_type):
    stream = Chunks([TOKEN.encode()])
    async def run():
        reader = make(reader_type, lambda req: httpx.Response(200, stream=stream,
            headers={"Content-Encoding": "gzip"}))
        try:
            with pytest.raises(ZenTaoProtocolError) as caught:
                await send(reader)
            assert TOKEN not in "".join(traceback.format_exception(caught.value))
        finally:
            await reader.close()
    asyncio.run(run())
    assert stream.closed


def test_nonfinite_numeric_overflow_is_rejected(reader_type):
    async def run():
        reader = make(reader_type, lambda req: httpx.Response(200,
            content=b'{"status":"success","n":1e999}'))
        try:
            with pytest.raises(ZenTaoProtocolError):
                await send(reader)
        finally:
            await reader.close()
    asyncio.run(run())


def test_unencodable_token_is_sanitized(reader_type):
    with pytest.raises(ValueError, match="readonly_token_invalid"):
        reader_type(origin=ORIGIN, token="secret\ud800", scope=SCOPE, actor=ACTOR)


def test_stream_over_limit_stops_consuming_and_closes(reader_type):
    stream = Chunks([b'x' * 1048576, b'x', b'never read'])
    async def run():
        reader = make(reader_type, lambda req: httpx.Response(200, stream=stream))
        try:
            with pytest.raises(ZenTaoProtocolError, match="readonly_response_too_large"):
                await send(reader)
        finally:
            await reader.close()
    asyncio.run(run())
    assert stream.closed and stream.read_count == 2


def test_midstream_timeout_is_sanitized_and_closed(reader_type):
    class Broken(Chunks):
        async def __aiter__(self):
            yield b'{"status":'
            raise httpx.ReadTimeout(TOKEN + ORIGIN)
    stream = Broken([])
    async def run():
        reader = make(reader_type, lambda req: httpx.Response(200, stream=stream))
        try:
            with pytest.raises(ZenTaoUnavailable) as caught:
                await send(reader)
            assert TOKEN not in "".join(traceback.format_exception(caught.value))
        finally:
            await reader.close()
    asyncio.run(run())
    assert stream.closed
