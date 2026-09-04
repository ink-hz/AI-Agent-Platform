from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from ipaddress import ip_network
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urljoin, urlsplit
from uuid import uuid4

import pytest
from app.control_plane.authorization import AuthorizationService
from app.control_plane.fae_access import FaeWorkbenchAccessUnavailable
from app.control_plane.middleware import IdentitySecurityMiddleware
from app.control_plane.models import AuthContext, IdentityMode, IssuedWebSession, Role
from app.control_plane.routes_auth import build_auth_router
from app.main import create_app
from app.voc_extension.client import VocUpstreamResponse
from app.voc_extension.internal_identity import capabilities_for
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

AI_ADMIN_ACCOUNT_CONTRACT_FIELDS = {
    "internal_user_id",
    "display_name",
    "role",
    "departments",
    "gender",
    "real_name",
    "mobile",
    "primary_department",
    "observation_agent_ids",
    "directory_freshness",
    "hard_stale_read_only",
    "csrf_token",
}
AI_ADMIN_ACCOUNT_CONTRACT_ROLES = {
    "member",
    "management_viewer",
    "platform_admin",
    "platform_owner",
}


class _NoObservationGrants:
    def permits(self, *_args):
        return False


class _NoFaeAccess:
    def allows(self, context):
        return context.role is Role.PLATFORM_OWNER


class _GrantingFaeAccess:
    def __init__(self, allowed_user_id):
        self.allowed_user_id = allowed_user_id

    def allows(self, context):
        return context.role is Role.PLATFORM_OWNER or (
            context.internal_user_id == self.allowed_user_id
        )


class _FailingFaeAccess:
    def allows(self, _context):
        raise FaeWorkbenchAccessUnavailable("fae grant repository unavailable")


class _MutableFaeAccess:
    def __init__(self) -> None:
        self.allowed_user_ids = set()

    def allows(self, context):
        return context.role is Role.PLATFORM_OWNER or (
            context.internal_user_id in self.allowed_user_ids
        )


class _FaeOverview:
    async def overview(self, _now):
        return {"agent_id": "ai-fae-agent"}


class _VocUpstream:
    def __init__(self) -> None:
        self.calls = []

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return VocUpstreamResponse(
            200,
            json.dumps({"items": [], "next_cursor": None}).encode(),
        )


class _VocDirectory:
    def names_for(self, _ids):
        return {}


class _FaeLaunch:
    def __init__(self) -> None:
        self.calls = []

    def issue(self, context, agent_id):
        self.calls.append((context.internal_user_id, agent_id))
        return SimpleNamespace(
            launch_url=(
                "https://agent.orbbec.com.cn/fae/"
                f"#platform_launch={'l' * 43}"
            ),
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(
    ("method", "route"),
    [
        ("GET", "/api/v1/catalog/agents"),
        ("GET", "/api/v1/brain/missions"),
        ("POST", "/api/v1/brain/missions"),
        ("GET", "/api/v1/brain/missions/{mission_id}"),
        ("GET", "/api/v1/brain/missions/{mission_id}/events"),
        ("POST", "/api/v1/brain/missions/{mission_id}/cancel"),
        ("POST", "/api/v1/agents/{agent_id}/missions"),
        ("POST", "/api/v1/conversations"),
        ("GET", "/api/v1/conversations"),
        ("GET", "/api/v1/conversations/{conversation_id}"),
        ("PATCH", "/api/v1/conversations/{conversation_id}"),
        ("GET", "/api/v1/conversations/{conversation_id}/messages"),
        ("POST", "/api/v1/conversations/{conversation_id}/messages"),
        ("GET", "/api/v1/conversations/{conversation_id}/events"),
        ("POST", "/api/v1/conversations/{conversation_id}/turns/current/cancel"),
        ("POST", "/api/v1/conversations/{conversation_id}/turns/{turn_id}/retry"),
        ("POST", "/api/v1/conversations/{conversation_id}/archive"),
        ("POST", "/api/v1/conversations/{conversation_id}/restore"),
        ("POST", "/api/v1/agents/{agent_id}/conversations"),
        ("GET", "/api/v1/ai-notes"),
        ("GET", "/api/v1/ai-notes/{category_slug}/{article_slug}"),
        ("GET", "/ai-notes"),
        ("GET", "/ai-notes/{client_path:path}"),
        ("GET", "/missions"),
        ("GET", "/missions/{client_path:path}"),
        ("GET", "/conversations"),
        ("GET", "/conversations/{client_path:path}"),
    ],
)
def test_agent_brain_routes_are_exact_authenticated_self_service_routes(
    role: Role, method: str, route: str
) -> None:
    context = AuthContext(uuid4(), role, uuid4(), False)
    service = AuthorizationService(_NoObservationGrants())

    decision = service.decide(context, method, route, ())

    assert decision.allowed is True
    assert decision.reason == "self_service"


def test_agent_brain_routes_do_not_authorize_nearby_or_worker_paths() -> None:
    context = AuthContext(uuid4(), Role.MEMBER, uuid4(), False)
    service = AuthorizationService(_NoObservationGrants())

    assert service.decide(
        context, "GET", "/api/v1/brain/missions/{mission_id}/debug", ()
    ).status_code == 403
    assert service.decide(
        context, "GET", "/api/v1/conversations/{conversation_id}/debug", ()
    ).status_code == 403
    assert service.decide(
        context, "POST", "/api/v1/execution-worker/lease", ()
    ).status_code == 403


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
        self.completed_app_id = None
        self.return_paths: dict[str, str] = {}
        self.started_count = 0
        self.gender = "female"
        self.real_name = "Platform Real Name"
        self.mobile = "13800138000"
        self.primary_department = "项目管理部"

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

    def in_client_configuration(self, return_path):
        if return_path == "/office/":
            return "office", "office-public-client-id"
        if return_path == "/voc/":
            return "voc", "voc-public-key"
        return "platform", self.app_key

    async def complete_in_client(self, code, *, app_id="platform"):
        self.provider_calls += 1
        self.completed_app_id = app_id
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
            "departments": ["产品中心", "项目管理部"],
            "gender": self.gender,
            "real_name": self.real_name,
            "mobile": self.mobile,
            "primary_department": self.primary_department,
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

    @router.get("/api/admin/fae/reports/latest")
    async def latest_fae_report():
        return None

    @router.get("/api/admin/fae/reports/{report_id}")
    async def fae_report(report_id: str):
        return report_id

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
    assert middleware._resolved_route(scope("/api/admin/fae/reports/latest")) == (
        "/api/admin/fae/reports/latest",
        {},
    )
    assert middleware._resolved_route(scope("/api/admin/fae/reports/report-2026-08")) == (
        "/api/admin/fae/reports/{report_id}",
        {"report_id": "report-2026-08"},
    )


def _app(
    tmp_path: Path,
    monkeypatch,
    auth: FakeAuth,
    *,
    registry_document: str = "version: 1\nagents: []\n",
    brain_enabled: bool = False,
    ai_notes_reader=None,
    agent_launch_service=None,
    partner_service=None,
    partner_provider=None,
    fae_access=None,
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
    monkeypatch.setenv(
        "PLATFORM_AGENT_BRAIN_ENABLED", "1" if brain_enabled else "0"
    )
    app = create_app(
        registry_path=str(registry),
        cluster_contract_path=str(contract),
        start_poller=False,
        identity_auth=auth,
        ai_notes_reader=ai_notes_reader,
        agent_launch_service=agent_launch_service,
        partner_service=partner_service,
        partner_provider=partner_provider,
    )
    app.state.fae_access = fae_access if fae_access is not None else _NoFaeAccess()
    return app


def _app_with_static(
    tmp_path: Path,
    monkeypatch,
    auth: FakeAuth,
    static: Path,
    *,
    ai_notes_reader=None,
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
        ai_notes_reader=ai_notes_reader,
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
        (FakeAuth(), "/", "/login", "/agents/voc/workspace"),
        (
            FakeAuth(
                mode=IdentityMode.PREVIEW,
                prefix="/_preview/dingtalk-r1/",
            ),
            "/_preview/dingtalk-r1/",
            "/_preview/dingtalk-r1/login",
            "/_preview/dingtalk-r1/agents/voc/workspace",
        ),
    )
    for auth, root, login_path, workspace_path in scenarios:
        client = TestClient(_app_with_static(tmp_path, monkeypatch, auth, static))
        root_response = client.get(root, follow_redirects=False)
        assert root_response.status_code == 302
        assert root_response.headers["location"] == login_path
        pages = (
            client.get(login_path),
            client.get(
                workspace_path,
                cookies={auth.cookie_name: "valid-cookie"},
            ),
        )
        for path, page in zip((login_path, workspace_path), pages, strict=True):
            assert page.status_code == 200
            parser = _AssetReferences()
            parser.feed(page.text)
            assert parser.values
            for reference in parser.values:
                resolved = urlsplit(
                    urljoin(f"https://agent.example.test{path}", reference)
                ).path
                assert resolved == root + "favicon.ico" or resolved.startswith(
                    root + "assets/"
                )
                assert client.get(resolved).status_code == 200
        login = pages[0]
        csp = login.headers["content-security-policy"]
        assert f"https://agent.example.test{root}assets/" in csp or (
            root == "/" and "script-src 'self'" in csp
        )


def test_article_body_requires_auth_and_is_absent_from_public_assets(
    tmp_path, monkeypatch
) -> None:
    sentinel = "INTERNAL_AI_NOTE_SENTINEL_8F2C"

    class SentinelReader(_AiNotesReader):
        def article(self, category_slug: str, article_slug: str):
            selected = super().article(category_slug, article_slug)
            return None if selected is None else {**selected, "markdown": sentinel}

    webui = Path(__file__).parents[2] / "webui"
    static = tmp_path / "vite-ai-notes-dist"
    subprocess.run(
        [
            "npm", "exec", "vite", "--", "build", "--outDir", str(static),
            "--emptyOutDir",
        ],
        cwd=webui,
        check=True,
        capture_output=True,
        text=True,
    )
    auth = FakeAuth()
    client = TestClient(
        _app_with_static(
            tmp_path,
            monkeypatch,
            auth,
            static,
            ai_notes_reader=SentinelReader(),
        )
    )

    assert client.get("/api/v1/ai-notes/foundations/handbook").status_code == 401
    assert client.get("/ai-notes").status_code == 401
    public_bytes = b"".join(
        path.read_bytes() for path in static.rglob("*") if path.is_file()
    )
    assert sentinel.encode() not in public_bytes

    cookies = {auth.cookie_name: "valid-cookie"}
    article = client.get(
        "/api/v1/ai-notes/foundations/handbook",
        cookies=cookies,
    )
    shell = client.get("/ai-notes/foundations/handbook", cookies=cookies)
    assert article.status_code == 200
    assert article.json()["markdown"] == sentinel
    assert shell.status_code == 200
    assert sentinel not in shell.text


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
        "app_id": "platform",
    }

    office_config = client.get(
        "/api/v1/auth/dingtalk/config?return_path=%2Foffice%2F"
    )
    assert office_config.json() == {
        "client_id": "office-public-client-id",
        "corp_id": "public-corp-id",
        "app_id": "office",
    }
    assert office_config.headers["cache-control"] == "no-store"

    voc_config = client.get(
        "/api/v1/auth/dingtalk/config?return_path=%2Fvoc%2F"
    )
    assert voc_config.json() == {
        "client_id": "voc-public-key",
        "corp_id": "public-corp-id",
        "app_id": "voc",
    }
    assert "voc-secret" not in voc_config.text

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


@pytest.mark.parametrize(
    "path",
    [
        "/hr",
        "/hr/",
        "/hr/chat",
        "/hr/positions/11111111-1111-4111-8111-111111111111",
        "/hr/positions/11111111-1111-4111-8111-111111111111/conversations/hr%3Aone",
        "/hr/conversations/hr%3Aone",
        "/marketing",
        "/marketing/",
        "/marketing/prospecting",
        "/marketing/inbound",
        "/marketing/voice",
        "/marketing/intelligence",
        "/marketing/gtm",
        "/marketing/voice/conversations/mkt%3Aone",
    ],
)
def test_public_hr_and_marketing_shells_bootstrap_enterprise_login(
    tmp_path, monkeypatch, path: str
) -> None:
    client = TestClient(_app(tmp_path, monkeypatch, FakeAuth()))

    response = client.get(path)

    assert response.status_code == 200
    assert "LOGIN SHELL" in response.text
    assert 'name="platform-identity-mode" content="enabled"' in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "path",
    [
        "/hr/unknown/path",
        "/hr/positions/not-a-uuid",
        "/hr/positions/11111111-1111-4111-8111-111111111111/unknown",
        "/marketing/unknown",
        "/marketing/voice/unknown",
    ],
)
def test_nearby_unknown_workspace_shells_remain_protected(
    tmp_path, monkeypatch, path: str
) -> None:
    client = TestClient(_app(tmp_path, monkeypatch, FakeAuth()))

    assert client.get(path).status_code == 401


@pytest.mark.parametrize(
    "query",
    (
        "return_path=%2Fvoc%2F&return_path=%2Foffice%2F",
        "return_path=%2Foffice%2F&return_path=%2Fvoc%2F",
    ),
)
def test_public_dingtalk_config_uses_platform_app_for_duplicate_return_paths(
    tmp_path, monkeypatch, query: str
) -> None:
    client = TestClient(_app(tmp_path, monkeypatch, FakeAuth()))

    response = client.get(f"/api/v1/auth/dingtalk/config?{query}")

    assert response.json() == {
        "client_id": "public-client-id",
        "corp_id": "public-corp-id",
        "app_id": "platform",
    }
    assert "voc-secret" not in response.text


def test_authenticated_root_and_product_routes_serve_identity_shell(
    tmp_path, monkeypatch
) -> None:
    auth = FakeAuth()
    client = TestClient(_app(tmp_path, monkeypatch, auth, brain_enabled=False))
    cookies = {auth.cookie_name: "valid-cookie"}

    root = client.get("/", cookies=cookies, follow_redirects=False)
    assert root.status_code == 200
    assert "img-src 'self' data:" in root.headers["content-security-policy"]
    assert "x-platform-entry-state" not in root.headers
    assert "LOGIN SHELL" in root.text
    assert "platform-agent-brain-mode" not in root.text
    for path in (
        "/account", "/agents", "/agents/hr-bot", "/missions", "/conversations",
        "/hr", "/hr/", "/hr/conversations/hr%3Aone",
        "/marketing", "/marketing/", "/marketing/voice/conversations/mkt%3Aone",
        "/fae/manage", "/fae/manage/", "/fae/manage/reports/weekly-1",
        "/ai-notes", "/ai-notes/foundations/handbook",
        "/missions/00000000-0000-0000-0000-000000000001", "/admin",
        "/conversations/00000000-0000-0000-0000-000000000001",
        "/admin/agents", "/admin/sessions/fae%3Aone", "/sessions",
        "/sessions/fae%3Aone", "/review", "/activity", "/identity",
        "/governance",
    ):
        response = client.get(path, cookies=cookies)
        assert response.status_code == 200, path
        assert "LOGIN SHELL" in response.text
        assert 'name="platform-identity-mode" content="enabled"' in response.text
    for path in ("/fae/", "/fae/conversations/fae%3Aone", "/voc/", "/voc/manage/"):
        assert client.get(path, cookies=cookies).status_code in {403, 404}, path
    assert client.get("/unknown", cookies=cookies).status_code in {403, 404}


class _AiNotesReader:
    def index(self):
        return {"categories": []}

    def article(self, category_slug: str, article_slug: str):
        if (category_slug, article_slug) != ("foundations", "handbook"):
            return None
        return {
            "slug": "handbook",
            "title": "手册",
            "filename": "handbook.md",
            "description": "说明",
            "published_at": "2026-08-27",
            "updated_at": None,
            "tags": [],
            "reading_minutes": 1,
            "category_slug": "foundations",
            "category_title": "基础与原理",
            "markdown": "# INTERNAL_BODY_SENTINEL",
        }


@pytest.mark.parametrize("role", list(Role))
def test_ai_notes_routes_require_auth_and_allow_every_role(
    tmp_path, monkeypatch, role: Role
) -> None:
    auth = FakeAuth()
    auth.context = AuthContext(uuid4(), role, uuid4(), False)
    client = TestClient(
        _app(tmp_path, monkeypatch, auth, ai_notes_reader=_AiNotesReader())
    )
    cookies = {auth.cookie_name: "valid-cookie"}

    assert client.get("/api/v1/ai-notes").status_code == 401
    index = client.get("/api/v1/ai-notes", cookies=cookies)
    article = client.get(
        "/api/v1/ai-notes/foundations/handbook", cookies=cookies
    )

    assert index.status_code == 200
    assert index.headers["cache-control"] == "no-store"
    assert article.status_code == 200
    assert article.json()["markdown"] == "# INTERNAL_BODY_SENTINEL"


def test_ai_notes_failure_does_not_break_health_or_account(
    tmp_path, monkeypatch
) -> None:
    class BrokenReader:
        def index(self):
            raise RuntimeError("private content")

        def article(self, category_slug: str, article_slug: str):
            raise RuntimeError(f"private content {category_slug} {article_slug}")

    auth = FakeAuth()
    client = TestClient(
        _app(tmp_path, monkeypatch, auth, ai_notes_reader=BrokenReader())
    )
    cookies = {auth.cookie_name: "valid-cookie"}

    assert client.get("/api/v1/ai-notes", cookies=cookies).status_code == 503
    assert client.get("/api/health").status_code == 200
    assert client.get("/account", cookies=cookies).status_code == 200


def test_authenticated_root_serves_brain_shell_without_release_metadata(
    tmp_path
) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<main>BRAIN SHELL</main>", encoding="utf-8")
    auth = FakeAuth()
    app = FastAPI()
    app.include_router(
        build_auth_router(
            auth,
            static_dir=str(static),
            public_assets=frozenset(),
            detailed_health=lambda _request: {},
        )
    )

    response = TestClient(app).get(
        "/", cookies={auth.cookie_name: "valid-cookie"}, follow_redirects=False
    )

    assert response.status_code == 200
    assert "BRAIN SHELL" in response.text
    assert "platform-agent-brain-mode" not in response.text


def test_authenticated_root_never_falls_back_to_a_preparing_state(
    tmp_path, monkeypatch
) -> None:
    auth = FakeAuth()
    client = TestClient(_app(tmp_path, monkeypatch, auth, brain_enabled=False))

    response = client.get(
        "/", cookies={auth.cookie_name: "valid-cookie"}, follow_redirects=False
    )

    assert response.status_code == 200
    assert "x-platform-entry-state" not in response.headers
    assert "platform-agent-brain-mode" not in response.text
    assert response.headers["cache-control"] == "no-store"


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
        client.post(
            "/api/v1/auth/logout",
            cookies=session_cookies,
            headers={"Origin": auth.public_base_url, "X-CSRF-Token": "wrong"},
        ),
        client.get(
            "/api/v1/manage/system-health", cookies=session_cookies
        ),
    ]

    account = client.get("/api/v1/account", cookies=session_cookies)
    assert account.headers.get("cache-control") == "private, no-store"
    assert account.headers.get("pragma") == "no-cache"

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
        json={"code": "code", "app_id": "office"},
        headers={"Origin": "https://agent.example.test"},
    )
    assert in_client.status_code == 200
    assert in_client.json() == {"csrf_token": "csrf-value"}
    assert auth.provider_calls == 2
    assert auth.completed_app_id == "office"


def test_in_client_exchange_defaults_to_platform_and_rejects_invalid_app_id(
    tmp_path, monkeypatch
) -> None:
    auth = FakeAuth()
    client = TestClient(_app(tmp_path, monkeypatch, auth))
    headers = {"Origin": "https://agent.example.test"}

    defaulted = client.post(
        "/api/v1/auth/dingtalk/in-client/exchange",
        json={"code": "code"},
        headers=headers,
    )
    invalid = client.post(
        "/api/v1/auth/dingtalk/in-client/exchange",
        json={"code": "provider-code-secret", "app_id": "Office!"},
        headers=headers,
    )

    assert defaulted.status_code == 200
    assert auth.completed_app_id == "platform"
    assert invalid.status_code == 422
    assert "provider-code-secret" not in invalid.text


def test_public_dingtalk_config_rejects_unsafe_return_path_without_echo(
    tmp_path, monkeypatch
) -> None:
    auth = FakeAuth()

    def reject_unsafe(return_path):
        if return_path and "secret" in return_path:
            raise ValueError("provider-secret-must-not-escape")
        return "platform", auth.app_key

    auth.in_client_configuration = reject_unsafe
    response = TestClient(_app(tmp_path, monkeypatch, auth)).get(
        "/api/v1/auth/dingtalk/config?return_path=%2Fprovider-secret"
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "login request invalid"}
    assert "provider-secret" not in response.text


def test_account_logout_csrf_origin_and_server_revocation(tmp_path, monkeypatch) -> None:
    auth = FakeAuth()
    client = TestClient(_app(tmp_path, monkeypatch, auth))
    cookies = {
        auth.cookie_name: "valid-cookie",
        auth.csrf_cookie_name: auth.csrf,
    }

    account = client.get("/api/v1/account", cookies=cookies)
    assert account.status_code == 200
    payload = account.json()
    assert set(payload) == AI_ADMIN_ACCOUNT_CONTRACT_FIELDS
    gender = payload.pop("gender")
    if gender != "female":
        pytest.fail("account gender projection mismatch")
    assert payload.pop("real_name") == "Platform Real Name"
    assert payload.pop("mobile") == "13800138000"
    assert payload.pop("primary_department") == "项目管理部"
    assert payload == {
        "internal_user_id": str(auth.context.internal_user_id),
        "display_name": "Platform user",
        "role": "platform_owner",
        "departments": ["产品中心", "项目管理部"],
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


def test_page_access_event_csrf_exception_is_exact_and_keeps_origin_and_session() -> None:
    from app.control_plane.middleware import is_page_access_event_request

    assert is_page_access_event_request(
        "POST", "/api/v1/access-events/page-view"
    ) is True
    for method, path in (
        ("GET", "/api/v1/access-events/page-view"),
        ("POST", "/api/v1/access-events/page-view/"),
        ("POST", "/api/v1/access-events/page-view/extra"),
        ("POST", "/api/v1/access-events/page-views"),
    ):
        assert is_page_access_event_request(method, path) is False

    class Limiter:
        def __init__(self) -> None:
            self.calls = []

        def check_authenticated(self, actor, *, mutation):
            self.calls.append((actor, mutation))

    auth = FakeAuth()
    limiter = Limiter()
    auth.rate_limiter = limiter
    app = FastAPI()

    @app.post("/api/v1/access-events/page-view", status_code=204)
    async def page_view_event():
        return None

    app.add_middleware(
        IdentitySecurityMiddleware,
        auth=auth,
        public_assets=frozenset(),
        authorization=AuthorizationService(_NoObservationGrants()),
        routes=tuple(app.router.routes),
    )
    client = TestClient(app)
    cookies = {auth.cookie_name: "valid-cookie"}

    accepted = client.post(
        "/api/v1/access-events/page-view",
        headers={"Origin": auth.public_base_url},
        cookies=cookies,
    )
    assert accepted.status_code == 204
    assert limiter.calls == []
    assert client.post(
        "/api/v1/access-events/page-view",
        headers={"Origin": "https://fae.orbbec.com.cn"},
        cookies=cookies,
    ).status_code == 403
    assert client.post(
        "/api/v1/access-events/page-view",
        cookies=cookies,
    ).status_code == 403
    assert client.post(
        "/api/v1/access-events/page-view",
        headers={"Origin": auth.public_base_url},
    ).status_code == 401


def test_platform_app_mounts_access_history_routes_with_fail_closed_backend(
    tmp_path, monkeypatch
) -> None:
    auth = FakeAuth()
    client = TestClient(_app(tmp_path, monkeypatch, auth))
    cookies = {auth.cookie_name: "valid-cookie"}

    page = client.post(
        "/api/v1/access-events/page-view",
        headers={"Origin": auth.public_base_url},
        cookies=cookies,
        json={
            "access_event_id": str(uuid4()),
            "workspace_key": "platform",
            "page_key": "platform.brain",
            "agent_id": None,
        },
    )
    assert page.status_code == 503
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["pragma"] == "no-cache"
    query = client.get(
        "/api/v1/manage/access-events", cookies=cookies
    )
    assert query.status_code == 503
    assert query.headers["cache-control"] == "no-store"
    assert query.headers["pragma"] == "no-cache"

    auth.context = AuthContext(uuid4(), Role.PLATFORM_ADMIN, uuid4(), False)
    assert client.get(
        "/api/v1/manage/access-events", cookies=cookies
    ).status_code == 403


def test_ai_admin_account_contract_roles_match_complete_platform_role_enum() -> None:
    assert AI_ADMIN_ACCOUNT_CONTRACT_ROLES == {role.value for role in Role}


@pytest.mark.parametrize("role", tuple(Role))
def test_account_serializes_every_ai_admin_contract_role_with_exact_fields(
    tmp_path,
    monkeypatch,
    role,
) -> None:
    auth = FakeAuth()
    auth.context = AuthContext(
        auth.context.internal_user_id,
        role,
        auth.context.session_id,
        False,
    )
    response = TestClient(_app(tmp_path, monkeypatch, auth)).get(
        "/api/v1/account",
        cookies={auth.cookie_name: "valid-cookie"},
    )

    assert response.status_code == 200
    assert set(response.json()) == AI_ADMIN_ACCOUNT_CONTRACT_FIELDS
    assert response.json()["role"] == role.value


def test_account_projects_only_bounded_fae_workspace_scope(
    tmp_path,
    monkeypatch,
) -> None:
    auth = FakeAuth()
    granted_user_id = uuid4()
    fae_access = _GrantingFaeAccess(granted_user_id)
    client = TestClient(_app(tmp_path, monkeypatch, auth, fae_access=fae_access))

    auth.context = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
    owner = client.get(
        "/api/v1/account",
        headers={"X-Platform-Account-Contract": "2"},
        cookies={auth.cookie_name: "valid-cookie"},
    )
    auth.context = AuthContext(granted_user_id, Role.MEMBER, uuid4(), False)
    granted_member = client.get(
        "/api/v1/account",
        headers={"X-Platform-Account-Contract": "2"},
        cookies={auth.cookie_name: "valid-cookie"},
    )
    for role in (Role.MEMBER, Role.MANAGEMENT_VIEWER, Role.PLATFORM_ADMIN):
        auth.context = AuthContext(uuid4(), role, uuid4(), False)
        denied = client.get(
            "/api/v1/account",
            headers={"X-Platform-Account-Contract": "2"},
            cookies={auth.cookie_name: "valid-cookie"},
        )
        assert denied.status_code == 200
        assert denied.json()["workspace_scopes"] == []

    assert owner.status_code == 200
    assert owner.json()["workspace_scopes"] == ["fae_workbench"]
    assert granted_member.status_code == 200
    assert granted_member.json()["workspace_scopes"] == ["fae_workbench"]


def test_fae_navigation_projects_only_the_bounded_management_url(
    tmp_path,
    monkeypatch,
) -> None:
    auth = FakeAuth()
    granted_user_id = uuid4()
    client = TestClient(
        _app(
            tmp_path,
            monkeypatch,
            auth,
            fae_access=_GrantingFaeAccess(granted_user_id),
        )
    )
    cookies = {auth.cookie_name: "valid-cookie"}

    auth.context = AuthContext(uuid4(), Role.PLATFORM_OWNER, uuid4(), False)
    owner = client.get("/api/v1/workspaces/fae/navigation", cookies=cookies)
    auth.context = AuthContext(granted_user_id, Role.MEMBER, uuid4(), False)
    granted_member = client.get(
        "/api/v1/workspaces/fae/navigation", cookies=cookies
    )
    auth.context = AuthContext(uuid4(), Role.MEMBER, uuid4(), False)
    ordinary_member = client.get(
        "/api/v1/workspaces/fae/navigation", cookies=cookies
    )

    assert owner.status_code == 200
    assert owner.json() == {"management_workspace_url": "/fae/manage/"}
    assert granted_member.status_code == 200
    assert granted_member.json() == {
        "management_workspace_url": "/fae/manage/"
    }
    assert ordinary_member.status_code == 200
    assert ordinary_member.json() == {"management_workspace_url": None}
    for response in (owner, granted_member, ordinary_member):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"


def test_fae_navigation_requires_authentication(tmp_path, monkeypatch) -> None:
    auth = FakeAuth()
    response = TestClient(_app(tmp_path, monkeypatch, auth)).get(
        "/api/v1/workspaces/fae/navigation"
    )

    assert response.status_code == 401


def test_fae_navigation_fails_closed_without_leaking_account_data(
    tmp_path,
    monkeypatch,
) -> None:
    auth = FakeAuth()
    response = TestClient(
        _app(tmp_path, monkeypatch, auth, fae_access=_FailingFaeAccess())
    ).get(
        "/api/v1/workspaces/fae/navigation",
        cookies={auth.cookie_name: "valid-cookie"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "workspace navigation unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


@pytest.mark.parametrize(
    ("role", "has_fae_grant", "fae_status", "voc_status"),
    (
        (Role.PLATFORM_OWNER, False, 200, 200),
        (Role.MEMBER, True, 200, 403),
        (Role.MANAGEMENT_VIEWER, False, 403, 200),
        (Role.PLATFORM_ADMIN, False, 403, 200),
        (Role.MEMBER, False, 403, 403),
    ),
)
def test_fae_and_voc_management_scopes_are_independent(
    tmp_path,
    monkeypatch,
    role,
    has_fae_grant,
    fae_status,
    voc_status,
) -> None:
    auth = FakeAuth()
    access = _MutableFaeAccess()
    auth.context = AuthContext(uuid4(), role, uuid4(), False)
    if has_fae_grant:
        access.allowed_user_ids.add(auth.context.internal_user_id)
    app = _app(tmp_path, monkeypatch, auth, fae_access=access)
    app.state.fae_workbench_service = _FaeOverview()
    voc_upstream = _VocUpstream()
    app.state.voc_extension_client = voc_upstream
    app.state.voc_submitter_directory = _VocDirectory()
    client = TestClient(app)
    cookies = {auth.cookie_name: "valid-cookie"}

    fae_response = client.get("/api/fae/overview", cookies=cookies)
    voc_response = client.get(
        "/api/v1/extensions/voc/admin/vocs",
        cookies=cookies,
    )

    assert fae_response.status_code == fae_status
    assert voc_response.status_code == voc_status
    assert ("voc.read_all" in capabilities_for(auth.context)) is (
        voc_status == 200
    )
    assert len(voc_upstream.calls) == (1 if voc_status == 200 else 0)
    if fae_status == 403:
        assert fae_response.json() == {
            "detail": "fae workbench access required"
        }


def test_revoked_fae_grant_denies_the_next_request_without_breaking_direct_use(
    tmp_path,
    monkeypatch,
) -> None:
    auth = FakeAuth()
    auth.context = AuthContext(uuid4(), Role.MEMBER, uuid4(), False)
    access = _MutableFaeAccess()
    access.allowed_user_ids.add(auth.context.internal_user_id)
    launch = _FaeLaunch()
    app = _app(
        tmp_path,
        monkeypatch,
        auth,
        fae_access=access,
        agent_launch_service=launch,
    )
    app.state.fae_workbench_service = _FaeOverview()
    client = TestClient(app)
    cookies = {auth.cookie_name: "valid-cookie"}

    assert client.get("/api/fae/overview", cookies=cookies).status_code == 200
    access.allowed_user_ids.remove(auth.context.internal_user_id)
    revoked = client.get("/api/fae/overview", cookies=cookies)
    direct_use = client.post(
        "/api/v1/agents/ai-fae-agent/launch",
        cookies={**cookies, auth.csrf_cookie_name: auth.csrf},
        headers={
            "Origin": auth.public_base_url,
            "X-CSRF-Token": auth.csrf,
        },
    )

    assert revoked.status_code == 403
    assert revoked.json() == {"detail": "fae workbench access required"}
    assert direct_use.status_code == 200
    assert direct_use.json()["launch_url"] == (
        "https://agent.orbbec.com.cn/fae/"
        f"#platform_launch={'l' * 43}"
    )
    assert launch.calls == [(auth.context.internal_user_id, "ai-fae-agent")]


def test_account_v1_remains_available_when_fae_scope_repository_fails(
    tmp_path,
    monkeypatch,
) -> None:
    auth = FakeAuth()
    response = TestClient(
        _app(tmp_path, monkeypatch, auth, fae_access=_FailingFaeAccess())
    ).get(
        "/api/v1/account",
        cookies={auth.cookie_name: "valid-cookie"},
    )

    assert response.status_code == 200
    assert set(response.json()) == AI_ADMIN_ACCOUNT_CONTRACT_FIELDS


def test_account_v2_fails_closed_when_fae_scope_repository_fails(
    tmp_path,
    monkeypatch,
) -> None:
    auth = FakeAuth()
    response = TestClient(
        _app(tmp_path, monkeypatch, auth, fae_access=_FailingFaeAccess())
    ).get(
        "/api/v1/account",
        headers={"X-Platform-Account-Contract": "2"},
        cookies={auth.cookie_name: "valid-cookie"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "account unavailable"}


def test_account_returns_null_gender_without_changing_private_cache_contract(
    tmp_path,
    monkeypatch,
) -> None:
    auth = FakeAuth()
    auth.gender = None
    auth.real_name = None
    auth.mobile = None
    auth.primary_department = None
    response = TestClient(_app(tmp_path, monkeypatch, auth)).get(
        "/api/v1/account",
        cookies={auth.cookie_name: "valid-cookie"},
    )

    assert response.status_code == 200
    assert set(response.json()) == AI_ADMIN_ACCOUNT_CONTRACT_FIELDS
    assert response.json()["gender"] is None
    assert response.json()["real_name"] is None
    assert response.json()["mobile"] is None
    assert response.json()["primary_department"] is None
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"


def test_account_failure_is_privacy_safe(tmp_path, monkeypatch, caplog) -> None:
    from app.control_plane.auth import AuthenticationError

    auth = FakeAuth()

    def fail_account_snapshot(_context):
        raise AuthenticationError(
            "employee record contained provider and gender details"
        )

    auth.account_snapshot = fail_account_snapshot
    with caplog.at_level("INFO"):
        response = TestClient(_app(tmp_path, monkeypatch, auth)).get(
            "/api/v1/account",
            cookies={auth.cookie_name: "valid-cookie"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "account unavailable"}
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    for forbidden in ("employee record", "provider", "gender details"):
        if forbidden in log_text:
            pytest.fail("account failure log exposed identity detail")


def test_loopback_session_subject_returns_only_minimal_verified_identity(
    tmp_path, monkeypatch
) -> None:
    auth = FakeAuth()
    auth.trusted_proxy_networks = (ip_network("127.0.0.1/32"),)
    client = TestClient(
        _app(tmp_path, monkeypatch, auth), client=("127.0.0.1", 51000)
    )
    response = client.get(
        "/api/v1/internal/session/subject",
        cookies={auth.cookie_name: "valid-cookie", auth.csrf_cookie_name: auth.csrf},
        headers={
            "X-Real-IP": "127.0.0.1",
            "X-Forwarded-Proto": "http",
            "X-Ignored-User-ID": "forged-user",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "internal_user_id": str(auth.context.internal_user_id),
        "display_name": "Platform user",
        "active": True,
    }
    assert "set-cookie" not in response.headers
    serialized = response.text.lower()
    for forbidden in (
        "mobile", "real_name", "department", "role", "csrf", "token",
        "13800138000", "platform real name", "forged-user",
    ):
        assert forbidden not in serialized


def test_session_subject_is_hidden_from_public_edges_and_fails_closed(
    tmp_path, monkeypatch
) -> None:
    from app.control_plane.auth import AuthenticationError

    auth = FakeAuth()
    auth.trusted_proxy_networks = (ip_network("127.0.0.1/32"),)
    client = TestClient(
        _app(tmp_path, monkeypatch, auth), client=("127.0.0.1", 51000)
    )
    cookies = {auth.cookie_name: "valid-cookie"}

    public_response = client.get(
        "/api/v1/internal/session/subject",
        cookies=cookies,
        headers={"X-Real-IP": "203.0.113.8", "X-Forwarded-Proto": "https"},
    )
    assert public_response.status_code == 404

    auth.revoked = True
    inactive = client.get(
        "/api/v1/internal/session/subject",
        cookies=cookies,
        headers={"X-Real-IP": "127.0.0.1", "X-Forwarded-Proto": "http"},
    )
    assert inactive.status_code == 401
    auth.revoked = False

    def fail_account_snapshot(_context):
        raise AuthenticationError("provider detail must remain private")

    auth.account_snapshot = fail_account_snapshot
    unavailable = client.get(
        "/api/v1/internal/session/subject",
        cookies=cookies,
        headers={"X-Real-IP": "127.0.0.1", "X-Forwarded-Proto": "http"},
    )
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "session subject unavailable"}


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
