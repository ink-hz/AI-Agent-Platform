from __future__ import annotations

from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import parse_qs, urljoin, urlsplit
from uuid import uuid4

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.control_plane.models import AuthContext, IdentityMode, IssuedWebSession, Role
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.main import create_app


class FakeAuth:
    def __init__(self, *, mode=IdentityMode.PRODUCTION, prefix="/") -> None:
        self.mode = mode
        self.route_prefix = prefix
        self.cookie_name = (
            "platform_preview_session"
            if mode is IdentityMode.PREVIEW
            else "__Host-platform_session"
        )
        self.csrf_cookie_name = (
            "platform_preview_csrf"
            if mode is IdentityMode.PREVIEW
            else "__Host-platform_csrf"
        )
        self.public_base_url = "https://agent.example.test"
        self.app_key = "public-client-id"
        self.corp_id = "public-corp-id"
        self.context = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
        self.csrf = "csrf-value"
        self.revoked = False
        self.provider_calls = 0
        self.return_paths: dict[str, str] = {}
        self.started_count = 0

    def start_qr(self, return_path):
        from app.control_plane.auth import StartedLogin
        self.started_count += 1
        state = f"state-{self.started_count}"
        self.return_paths[state] = return_path
        return StartedLogin(
            uuid4(), state, f"https://login.dingtalk.com/test?state={state}", return_path
        )

    async def complete_qr(self, state, code):
        from app.control_plane.auth import AuthenticationError, CompletedLogin
        try:
            return_path = self.return_paths.pop(state)
        except KeyError:
            raise AuthenticationError("login attempt invalid") from None
        self.provider_calls += 1
        return CompletedLogin(self._issued(), return_path)

    async def complete_in_client(self, code):
        self.provider_calls += 1
        return self._issued()

    def _issued(self):
        now = datetime.now(UTC)
        return IssuedWebSession(uuid4(), "new-cookie", self.csrf, now + timedelta(hours=8), now + timedelta(hours=24))

    def authenticate(self, token):
        if token == "valid-cookie" and not self.revoked:
            return self.context, self.csrf
        return None

    def logout(self, context):
        self.revoked = True

    def account_snapshot(self, context):
        return {
            "display_name": "Platform user",
            "observation_agent_ids": [],
            "directory_freshness": "hard_stale"
            if context.hard_stale_read_only else "fresh",
        }


def test_authorization_route_resolution_expands_included_fastapi_routers() -> None:
    router = APIRouter()

    @router.get("/account")
    async def account_shell():
        return None

    @router.get("/api/agents/{agent_id}/runtime")
    async def agent_runtime(agent_id: str):
        return agent_id

    app = FastAPI()
    app.include_router(router)
    middleware = IdentitySecurityMiddleware(
        app,
        auth=FakeAuth(),
        public_assets=frozenset(),
        authorization=object(),
        routes=tuple(app.router.routes),
    )

    def scope(path: str) -> dict:
        return {
            "type": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "method": "GET",
            "scheme": "https",
            "query_string": b"",
            "headers": [],
        }

    assert middleware._resolved_route(scope("/account")) == ("/account", {})
    assert middleware._resolved_route(scope("/api/agents/hr/runtime")) == (
        "/api/agents/{agent_id}/runtime",
        {"agent_id": "hr"},
    )


def _app(
    tmp_path: Path,
    monkeypatch,
    auth: FakeAuth,
    *,
    registry_document: str = "version: 1\nagents: []\n",
):
    static = tmp_path / "static"
    assets = static / "assets"
    assets.mkdir(parents=True)
    (static / "index.html").write_text("<main>LOGIN SHELL</main>", encoding="utf-8")
    (static / "favicon.ico").write_bytes(b"ico")
    (assets / "app-a1b2c3d4.js").write_text("console.log('ok')", encoding="utf-8")
    (assets / "hidden-deadbeef.js").write_text("console.log('hidden')", encoding="utf-8")
    (assets / "app.js").write_text("source", encoding="utf-8")
    (assets / "app-a1b2c3d4.js.map").write_text("map", encoding="utf-8")
    manifest = static / ".vite"
    manifest.mkdir()
    (manifest / "manifest.json").write_text(
        json.dumps({"index.html": {"file": "assets/app-a1b2c3d4.js", "isEntry": True}}),
        encoding="utf-8",
    )
    registry = tmp_path / "registry.yaml"
    registry.write_text(registry_document, encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"bots": []}), encoding="utf-8")
    monkeypatch.setenv("PLATFORM_STATIC_DIR", str(static))
    return create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
        identity_auth=auth,
    )


def _app_with_static(
    tmp_path: Path, monkeypatch, auth: FakeAuth, static: Path
):
    registry = tmp_path / f"registry-{auth.mode.value}.yaml"
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / f"contract-{auth.mode.value}.json"
    contract.write_text(json.dumps({"bots": []}), encoding="utf-8")
    monkeypatch.setenv("PLATFORM_STATIC_DIR", str(static))
    return create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
        identity_auth=auth,
    )


class _AssetReferences(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        for key, value in attrs:
            if key in {"src", "href"} and isinstance(value, str):
                self.values.append(value)


def test_one_real_vite_build_serves_assets_at_root_and_preview(
    tmp_path, monkeypatch
) -> None:
    webui = Path(__file__).parents[2] / "webui"
    static = tmp_path / "vite-dist"
    subprocess.run(
        [
            "npm",
            "exec",
            "vite",
            "--",
            "build",
            "--outDir",
            str(static),
            "--emptyOutDir",
        ],
        cwd=webui,
        check=True,
        capture_output=True,
        text=True,
    )

    scenarios = (
        (FakeAuth(), "/", "/login"),
        (
            FakeAuth(
                mode=IdentityMode.PREVIEW,
                prefix="/_preview/dingtalk-r1/",
            ),
            "/_preview/dingtalk-r1/",
            "/_preview/dingtalk-r1/login",
        ),
    )
    for auth, root, login_path in scenarios:
        client = TestClient(_app_with_static(tmp_path, monkeypatch, auth, static))
        root_response = client.get(root, follow_redirects=False)
        assert root_response.status_code == 302
        assert root_response.headers["location"] == login_path
        login = client.get(login_path)
        assert login.status_code == 200
        parser = _AssetReferences()
        parser.feed(login.text)
        assert parser.values
        for reference in parser.values:
            resolved = urlsplit(
                urljoin(f"https://agent.example.test{login_path}", reference)
            ).path
            assert resolved == root + "favicon.ico" or resolved.startswith(
                root + "assets/"
            )
            assert client.get(resolved).status_code == 200
        csp = login.headers["content-security-policy"]
        assert f"https://agent.example.test{root}assets/" in csp or (
            root == "/" and "script-src 'self'" in csp
        )


def test_exact_public_routes_and_root_redirect(tmp_path, monkeypatch) -> None:
    client = TestClient(_app(tmp_path, monkeypatch, FakeAuth()))

    root = client.get("/", follow_redirects=False)
    assert root.status_code == 302
    assert root.headers["location"] == "/login"
    assert client.get("/login").status_code == 200
    assert client.get("/favicon.ico").status_code == 200
    assert client.get("/assets/app-a1b2c3d4.js").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/v1/auth/dingtalk/config").json() == {
        "client_id": "public-client-id",
        "corp_id": "public-corp-id",
    }

    for path in (
        "/api/deployment",
        "/api/agents",
        "/manage",
        "/login/extra",
        "/assets/app.js",
        "/assets/app-a1b2c3d4.js.map",
        "/assets/hidden-deadbeef.js",
        "/assets/../index.html",
    ):
        assert client.get(path).status_code == 401, path


def test_authenticated_root_and_product_routes_serve_identity_shell(
    tmp_path, monkeypatch
) -> None:
    auth = FakeAuth()
    client = TestClient(_app(tmp_path, monkeypatch, auth))
    cookies = {auth.cookie_name: "valid-cookie"}

    root = client.get("/", cookies=cookies, follow_redirects=False)
    assert root.status_code == 200
    assert 'name="platform-identity-mode" content="enabled"' in root.text
    for path in (
        "/account", "/agents", "/agents/hr-bot/runtime", "/sessions",
        "/sessions/fae%3Aone", "/review", "/activity", "/identity",
        "/governance",
    ):
        response = client.get(path, cookies=cookies)
        assert response.status_code == 200, path
        assert "LOGIN SHELL" in response.text
        assert 'name="platform-identity-mode" content="enabled"' in response.text
    assert client.get("/unknown", cookies=cookies).status_code in {403, 404}


def test_manifest_authorized_asset_symlink_never_exposes_outside_content(
    tmp_path, monkeypatch
) -> None:
    app = _app(tmp_path, monkeypatch, FakeAuth())
    static = Path(os.environ["PLATFORM_STATIC_DIR"])
    target = static / "assets" / "app-a1b2c3d4.js"
    outside = tmp_path / "outside.js"
    outside.write_text("TOP SECRET", encoding="utf-8")
    target.unlink()
    target.symlink_to(outside)

    response = TestClient(app).get("/assets/app-a1b2c3d4.js")

    assert response.status_code != 200
    assert "TOP SECRET" not in response.text


def test_qr_start_uses_fixed_flow_safe_return_and_no_store(tmp_path, monkeypatch) -> None:
    auth = FakeAuth()
    client = TestClient(_app(tmp_path, monkeypatch, auth))

    response = client.post(
        "/api/v1/auth/dingtalk/start",
        json={"return_path": "/", "flow": "in_client"},
        headers={"Origin": "https://agent.example.test"},
    )

    assert response.status_code == 422
    response = client.post(
        "/api/v1/auth/dingtalk/start",
        json={"return_path": "/"},
        headers={"Origin": "https://agent.example.test"},
    )
    assert response.status_code == 200
    assert response.json()["authorization_url"].startswith("https://login.dingtalk.com/")
    assert response.headers["cache-control"] == "no-store"


def test_qr_callbacks_use_the_return_path_bound_to_each_state(tmp_path, monkeypatch) -> None:
    auth = FakeAuth()
    client = TestClient(_app(tmp_path, monkeypatch, auth))

    account_started = client.post(
        "/api/v1/auth/dingtalk/start",
        json={"return_path": "/account"},
        headers={"Origin": "https://agent.example.test"},
    )
    admin_started = client.post(
        "/api/v1/auth/dingtalk/start",
        json={"return_path": "/admin/"},
        headers={"Origin": "https://agent.example.test"},
    )
    account_state = parse_qs(urlsplit(account_started.json()["authorization_url"]).query)["state"][0]
    admin_state = parse_qs(urlsplit(admin_started.json()["authorization_url"]).query)["state"][0]

    admin_callback = client.get(
        f"/api/v1/auth/dingtalk/callback?state={admin_state}&code=admin-code",
        follow_redirects=False,
    )
    account_callback = client.get(
        f"/api/v1/auth/dingtalk/callback?state={account_state}&code=account-code",
        follow_redirects=False,
    )
    unknown_callback = client.get(
        "/api/v1/auth/dingtalk/callback?state=unknown-state-secret&code=unknown-code-secret",
        follow_redirects=False,
    )

    assert admin_callback.status_code == 302
    assert admin_callback.headers["location"] == "/admin/"
    assert account_callback.status_code == 302
    assert account_callback.headers["location"] == "/account"
    assert unknown_callback.status_code == 302
    assert unknown_callback.headers["location"] == "/login?error=1"
    assert "unknown-state-secret" not in unknown_callback.text
    assert "unknown-code-secret" not in unknown_callback.text
    assert "unknown-state-secret" not in unknown_callback.headers["location"]
    assert "unknown-code-secret" not in unknown_callback.headers["location"]


def test_every_identity_response_prevents_browser_or_proxy_caching(
    tmp_path, monkeypatch
) -> None:
    auth = FakeAuth()
    client = TestClient(_app(tmp_path, monkeypatch, auth))
    session_cookies = {
        auth.cookie_name: "valid-cookie",
        auth.csrf_cookie_name: auth.csrf,
    }
    responses = [
        client.get("/", follow_redirects=False),
        client.get("/login"),
        client.get("/api/health"),
        client.post(
            "/api/v1/auth/dingtalk/start",
            json={"unexpected": True},
            headers={"Origin": auth.public_base_url},
        ),
        client.get(
            "/api/v1/auth/dingtalk/callback?state=state&code=code",
            follow_redirects=False,
        ),
        client.post(
            "/api/v1/auth/dingtalk/in-client/exchange",
            json={},
            headers={"Origin": auth.public_base_url},
        ),
        client.get("/api/v1/account", cookies=session_cookies),
        client.post(
            "/api/v1/auth/logout",
            cookies=session_cookies,
            headers={"Origin": auth.public_base_url, "X-CSRF-Token": "wrong"},
        ),
        client.get(
            "/api/v1/manage/system-health", cookies=session_cookies
        ),
    ]

    for response in responses:
        assert response.headers.get("cache-control") == "no-store", (
            response.request.method,
            response.request.url,
            response.status_code,
        )
        assert response.headers.get("pragma") == "no-cache"


def test_callback_error_returns_to_login_and_never_exposes_provider_detail(
    tmp_path, monkeypatch
) -> None:
    from app.control_plane.auth import AuthenticationError

    auth = FakeAuth()

    async def rejected(_state, _code):
        raise AuthenticationError("login attempt invalid")

    auth.complete_qr = rejected
    response = TestClient(_app(tmp_path, monkeypatch, auth)).get(
        "/api/v1/auth/dingtalk/callback?state=unknown&code=secret-code",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=1"
    assert "secret-code" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


def test_duplicate_callback_with_valid_session_recovers_without_provider_exchange(
    tmp_path, monkeypatch
) -> None:
    auth = FakeAuth()
    response = TestClient(_app(tmp_path, monkeypatch, auth)).get(
        "/api/v1/auth/dingtalk/callback?state=already-used&code=already-used",
        cookies={auth.cookie_name: "valid-cookie"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/account"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert auth.provider_calls == 0


def test_qr_and_in_client_login_set_rotated_cookie_and_return_csrf(tmp_path, monkeypatch) -> None:
    auth = FakeAuth()
    client = TestClient(_app(tmp_path, monkeypatch, auth))

    started = client.post(
        "/api/v1/auth/dingtalk/start",
        json={"return_path": "/"},
        headers={"Origin": "https://agent.example.test"},
    )
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    qr = client.get(
        f"/api/v1/auth/dingtalk/callback?state={state}&code=code",
        follow_redirects=False,
    )
    assert qr.status_code == 302
    assert qr.headers["location"] == "/"
    assert "__Host-platform_session=new-cookie" in qr.headers["set-cookie"]
    assert "__Host-platform_csrf=csrf-value" in qr.headers["set-cookie"]
    assert "HttpOnly" in qr.headers["set-cookie"]
    assert "Secure" in qr.headers["set-cookie"]
    assert "SameSite=lax" in qr.headers["set-cookie"]
    assert "Path=/" in qr.headers["set-cookie"]

    in_client = client.post(
        "/api/v1/auth/dingtalk/in-client/exchange",
        json={"code": "code"},
        headers={"Origin": "https://agent.example.test"},
    )
    assert in_client.status_code == 200
    assert in_client.json() == {"csrf_token": "csrf-value"}
    assert auth.provider_calls == 2


def test_account_logout_csrf_origin_and_server_revocation(tmp_path, monkeypatch) -> None:
    auth = FakeAuth()
    client = TestClient(_app(tmp_path, monkeypatch, auth))
    cookies = {
        auth.cookie_name: "valid-cookie",
        auth.csrf_cookie_name: auth.csrf,
    }

    account = client.get("/api/v1/account", cookies=cookies)
    assert account.status_code == 200
    assert account.json() == {
        "internal_user_id": str(auth.context.internal_user_id),
        "display_name": "Platform user",
        "role": "platform_owner",
        "observation_agent_ids": [],
        "directory_freshness": "fresh",
        "hard_stale_read_only": False,
        "csrf_token": auth.csrf,
    }

    missing_csrf_cookie = client.get(
        "/api/v1/account", cookies={auth.cookie_name: "valid-cookie"}
    )
    assert missing_csrf_cookie.status_code == 200
    assert missing_csrf_cookie.json()["csrf_token"] == ""
    for headers in (
        {},
        {"Origin": "null", "X-CSRF-Token": auth.csrf},
        {"Origin": "https://evil.example", "X-CSRF-Token": auth.csrf},
        {"Origin": auth.public_base_url, "X-CSRF-Token": "wrong"},
        {
            "Origin": "http://agent.example.test",
            "X-CSRF-Token": auth.csrf,
            "X-Forwarded-Proto": "https",
            "X-Forwarded-For": "127.0.0.1",
        },
    ):
        assert client.post("/api/v1/auth/logout", headers=headers, cookies=cookies).status_code == 403

    result = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": auth.public_base_url, "X-CSRF-Token": auth.csrf},
        cookies=cookies,
    )
    assert result.status_code == 204
    assert auth.revoked
    assert client.get("/api/v1/account", cookies=cookies).status_code == 401


def test_account_serializes_platform_admin_role(tmp_path, monkeypatch) -> None:
    auth = FakeAuth()
    auth.context = AuthContext(
        auth.context.internal_user_id,
        Role.PLATFORM_ADMIN,
        auth.context.session_id,
        False,
    )
    response = TestClient(_app(tmp_path, monkeypatch, auth)).get(
        "/api/v1/account",
        cookies={auth.cookie_name: "valid-cookie"},
    )

    assert response.status_code == 200
    assert response.json()["role"] == "platform_admin"


def test_unknown_stored_role_fails_closed() -> None:
    with pytest.raises(ValueError):
        Role("unknown_stored_role")


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_every_authenticated_mutation_uses_origin_and_csrf(
    tmp_path, monkeypatch, method
) -> None:
    auth = FakeAuth()
    app = _app(tmp_path, monkeypatch, auth)
    app.add_api_route(
        "/api/test-mutation",
        lambda: {"status": "reached"},
        methods=[method.upper()],
    )
    client = TestClient(app)
    cookies = {auth.cookie_name: "valid-cookie"}

    assert getattr(client, method)(
        "/api/test-mutation", cookies=cookies
    ).status_code == 403
    assert getattr(client, method)(
        "/api/test-mutation",
        cookies=cookies,
        headers={"Origin": auth.public_base_url, "X-CSRF-Token": "wrong"},
    ).status_code == 403
    accepted = getattr(client, method)(
        "/api/test-mutation",
        cookies=cookies,
        headers={"Origin": auth.public_base_url, "X-CSRF-Token": auth.csrf},
    )
    assert accepted.status_code == 200
    assert accepted.json() == {"status": "reached"}


def test_protected_mutation_authenticates_before_origin_or_csrf(tmp_path, monkeypatch) -> None:
    auth = FakeAuth()
    app = _app(tmp_path, monkeypatch, auth)
    app.add_api_route("/api/test-mutation", lambda: None, methods=["POST"])
    client = TestClient(app)

    assert client.post("/api/test-mutation").status_code == 401


def test_detailed_health_is_owner_only_and_audited_fail_closed(tmp_path, monkeypatch) -> None:
    auth = FakeAuth()
    app = _app(tmp_path, monkeypatch, auth)
    client = TestClient(app)
    cookies = {auth.cookie_name: "valid-cookie"}

    response = client.get("/api/v1/manage/system-health", cookies=cookies)
    assert response.status_code == 503
    auth.context = AuthContext(uuid4(), Role.MEMBER, uuid4(), False)
    assert client.get("/api/v1/manage/system-health", cookies=cookies).status_code == 403


def test_preview_prefix_never_generates_root_urls_or_root_cookie(tmp_path, monkeypatch) -> None:
    prefix = "/_preview/dingtalk-r1/"
    auth = FakeAuth(mode=IdentityMode.PREVIEW, prefix=prefix)
    client = TestClient(_app(tmp_path, monkeypatch, auth))

    root = client.get(prefix, follow_redirects=False)
    assert root.status_code == 302
    assert root.headers["location"] == prefix + "login"
    response = client.post(
        prefix + "api/v1/auth/dingtalk/in-client/exchange",
        json={"code": "code"},
        headers={"Origin": auth.public_base_url},
    )
    assert response.status_code == 200
    assert "platform_preview_session=new-cookie" in response.headers["set-cookie"]
    assert "platform_preview_csrf=csrf-value" in response.headers["set-cookie"]
    assert "Path=/_preview/dingtalk-r1/" in response.headers["set-cookie"]
    assert client.get("/login").status_code == 401
    login = client.get(prefix + "login")
    assert "connect-src https://agent.example.test/_preview/dingtalk-r1/api/" in login.headers["content-security-policy"]
    assert "connect-src 'self'" not in login.headers["content-security-policy"]


def test_owner_detailed_health_is_returned_only_after_audit(tmp_path, monkeypatch) -> None:
    release_sha = "a" * 40
    monkeypatch.setenv("PLATFORM_RELEASE_SHA", release_sha)
    auth = FakeAuth()
    app = _app(
        tmp_path,
        monkeypatch,
        auth,
        registry_document="""
version: 1
agents:
  - id: marketing-gtm
    name: Marketing GTM
    entry_url: https://agent.example.test/agents/marketing-gtm
    health:
      url: http://127.0.0.1:9101/api/health
""",
    )
    audited = []
    app.state.system_health_audit = lambda context: audited.append(context.session_id)
    client = TestClient(app)

    response = client.get(
        "/api/v1/manage/system-health",
        cookies={auth.cookie_name: "valid-cookie"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    payload = response.json()
    assert payload["identity_mode"] == "production"
    assert payload["build"] == {
        "available": True,
        "release_name": "orbbec-agent-platform",
        "git_sha": release_sha,
    }
    assert payload["release"] == {
        "name": "orbbec-agent-platform",
        "version": "0.1.0",
        "git_sha": release_sha,
    }
    assert payload["deployment"]["mode"] == "local"
    assert payload["dependencies"]["registry"] == {
        "status": "ok",
        "agent_count": 1,
    }
    assert payload["agents"]["registered"] == [
        {
            "id": "marketing-gtm",
            "name": "Marketing GTM",
            "status": "active",
            "environment": "prod",
            "version": "",
        }
    ]
    assert [item["id"] for item in payload["agents"]["runtime"]] == [
        "marketing-gtm"
    ]
    assert payload["agents"]["local"]["summary"]["total"] == 0
    assert {agent["id"] for agent in payload["agents"]["remote"]["agents"]} == {
        "ai-fae-agent",
        "ai-admin-agent",
    }
    assert client.get("/api/health").json() == {"status": "ok"}
    assert audited == [auth.context.session_id]
