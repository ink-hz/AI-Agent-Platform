from __future__ import annotations

from contextlib import contextmanager
from hashlib import md5, sha256
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.control_plane.middleware import is_public_request
from app.control_plane.models import AuthContext, Role
from app.sdk_workbench.sso import SdkSsoService, build_sdk_sso_router


USER = UUID("11111111-1111-4111-8111-111111111111")
PLATFORM = "https://agent.orbbec.com.cn"
ZENTAO = "https://47.106.112.69"


class Repository:
    def __init__(self):
        self.pending = {}
        self.account = "cangyuan"
        self.can_issue = True
        self.bound = []
        self.login_slot_held = False

    def bind(self, actor, target, account):
        self.bound.append((actor, target, account))
        return {"internal_user_id": str(target), "account": account}

    def issue(self, actor, session_id, digest):
        if not self.can_issue:
            return False
        self.pending[digest] = self.account
        return True

    def consume(self, digest):
        return self.pending.pop(digest, None)

    def next_login_time(self):
        return 1792471200

    @contextmanager
    def login_slot(self):
        self.login_slot_held = True
        try:
            yield self.next_login_time()
        finally:
            self.login_slot_held = False


def build_app(tmp_path, repository, *, actor=USER, role=Role.MEMBER, stale=False, zentao_response=None):
    key = tmp_path / "entry-key"
    key.write_text("a" * 32, encoding="utf-8")
    seen = []

    def zen_reply(request):
        assert repository.login_slot_held
        seen.append(request)
        if zentao_response is not None:
            return zentao_response
        return httpx.Response(302, headers={"location": "/", "set-cookie": "zentaosid=0123456789abcdef0123456789abcdef; Path=/; HttpOnly"})

    service = SdkSsoService(
        repository, private_origin="http://zentao:80", public_origin=ZENTAO,
        platform_origin=PLATFORM, entry_code="sdk-sso", entry_key_file=key,
        transport=httpx.MockTransport(zen_reply),
    )
    app = FastAPI()

    @app.middleware("http")
    async def add_identity(request, call_next):
        if actor is not None:
            request.state.auth_context = AuthContext(actor, role, uuid4(), stale)
        return await call_next(request)

    app.include_router(build_sdk_sso_router(service))
    return app, seen


@pytest.mark.asyncio
async def test_authenticated_sdk_member_can_post_single_use_ticket_to_zentao(tmp_path):
    repository = Repository()
    app, seen = build_app(tmp_path, repository)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        start = await client.post("/api/v1/sdk/sso/start")
    assert start.status_code == 200
    payload = start.json()
    assert payload["consume_url"] == f"{ZENTAO}/sdk-sso/consume"
    assert "ticket" not in payload["consume_url"]
    assert sha256(payload["ticket"].encode()).digest() in repository.pending
    assert is_public_request("POST", "/api/v1/sdk/sso/consume", "/")
    assert not is_public_request("GET", "/api/v1/sdk/sso/consume", "/")

    async with AsyncClient(transport=ASGITransport(app=app), base_url=ZENTAO) as client:
        consume = await client.post(
            "/api/v1/sdk/sso/consume", data={"ticket": payload["ticket"]},
            headers={"origin": PLATFORM, "x-forwarded-proto": "https", "cookie": "zentaosid=old-session"},
            follow_redirects=False,
        )
        replay = await client.post(
            "/api/v1/sdk/sso/consume", data={"ticket": payload["ticket"]},
            headers={"origin": PLATFORM, "x-forwarded-proto": "https"},
        )
    assert consume.status_code == 303
    assert consume.headers["location"] == "/"
    assert "zentaosid=0123456789abcdef0123456789abcdef" in consume.headers["set-cookie"]
    for flag in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"):
        assert flag.lower() in consume.headers["set-cookie"].lower()
    assert consume.headers["referrer-policy"] == "no-referrer"
    assert replay.status_code == 403
    assert "set-cookie" not in replay.headers
    assert len(seen) == 1
    assert "cookie" not in seen[0].headers
    assert seen[0].url.host == "zentao"
    assert seen[0].url.params["account"] == "cangyuan"
    assert seen[0].url.params["token"] == md5(b"sdk-sso" + b"a" * 32 + b"1792471200").hexdigest()


@pytest.mark.asyncio
async def test_sso_denies_unbound_or_read_only_and_rejects_wrong_consume_origin(tmp_path):
    repository = Repository()
    repository.can_issue = False
    app, seen = build_app(tmp_path, repository)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        denied = await client.post("/api/v1/sdk/sso/start")
    assert denied.status_code == 403

    repository.can_issue = True
    stale_app, _ = build_app(tmp_path, repository, stale=True)
    async with AsyncClient(transport=ASGITransport(app=stale_app), base_url=PLATFORM) as client:
        stale = await client.post("/api/v1/sdk/sso/start")
    assert stale.status_code == 503

    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        ticket = (await client.post("/api/v1/sdk/sso/start")).json()["ticket"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ZENTAO) as client:
        wrong = await client.post("/api/v1/sdk/sso/consume", data={"ticket": ticket}, headers={"origin": "https://evil.example", "x-forwarded-proto": "https"})
        valid = await client.post("/api/v1/sdk/sso/consume", data={"ticket": ticket}, headers={"origin": PLATFORM, "x-forwarded-proto": "https"}, follow_redirects=False)
    assert wrong.status_code == 404
    assert valid.status_code == 303
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_bad_zentao_response_never_sets_browser_cookie(tmp_path):
    repository = Repository()
    app, _ = build_app(tmp_path, repository, zentao_response=httpx.Response(200, text="error"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url=PLATFORM) as client:
        ticket = (await client.post("/api/v1/sdk/sso/start")).json()["ticket"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ZENTAO) as client:
        result = await client.post("/api/v1/sdk/sso/consume", data={"ticket": ticket}, headers={"origin": PLATFORM, "x-forwarded-proto": "https"})
    assert result.status_code == 502
    assert "set-cookie" not in result.headers


@pytest.mark.asyncio
async def test_only_platform_owner_can_bind_member_to_zentao_account(tmp_path):
    repository = Repository()
    member_app, _ = build_app(tmp_path, repository)
    target = uuid4()
    async with AsyncClient(transport=ASGITransport(app=member_app), base_url=PLATFORM) as client:
        denied = await client.put(f"/api/v1/sdk/sso/bindings/{target}", json={"account": "cangyuan"})
    assert denied.status_code == 403
    owner_app, _ = build_app(tmp_path, repository, role=Role.PLATFORM_OWNER)
    async with AsyncClient(transport=ASGITransport(app=owner_app), base_url=PLATFORM) as client:
        allowed = await client.put(f"/api/v1/sdk/sso/bindings/{target}", json={"account": "cangyuan"})
    assert allowed.status_code == 200
    assert repository.bound == [(USER, target, "cangyuan")]
