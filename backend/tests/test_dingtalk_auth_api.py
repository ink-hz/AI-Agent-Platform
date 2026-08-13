from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.control_plane.models import AuthContext, IdentityMode, IssuedWebSession, Role
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
        self.context = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
        self.csrf = "csrf-value"
        self.revoked = False
        self.provider_calls = 0

    def start_qr(self, return_path):
        from app.control_plane.auth import StartedLogin
        return StartedLogin(uuid4(), "state-value", "https://login.dingtalk.com/test", return_path)

    async def complete_qr(self, state, code):
        self.provider_calls += 1
        return self._issued()

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


def _app(tmp_path: Path, monkeypatch, auth: FakeAuth):
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
    registry.write_text("version: 1\nagents: []\n", encoding="utf-8")
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"bots": []}), encoding="utf-8")
    monkeypatch.setenv("PLATFORM_STATIC_DIR", str(static))
    return create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
        identity_auth=auth,
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


def test_qr_and_in_client_login_set_rotated_cookie_and_return_csrf(tmp_path, monkeypatch) -> None:
    auth = FakeAuth()
    client = TestClient(_app(tmp_path, monkeypatch, auth))

    qr = client.get("/api/v1/auth/dingtalk/callback?state=state&code=code", follow_redirects=False)
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
    assert account.json()["role"] == "platform_owner"
    assert account.json()["csrf_token"] == auth.csrf

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
    auth = FakeAuth()
    app = _app(tmp_path, monkeypatch, auth)
    audited = []
    app.state.system_health_audit = lambda context: audited.append(context.session_id)
    client = TestClient(app)

    response = client.get(
        "/api/v1/manage/system-health",
        cookies={auth.cookie_name: "valid-cookie"},
    )

    assert response.status_code == 200
    assert response.json()["identity_mode"] == "production"
    assert audited == [auth.context.session_id]
