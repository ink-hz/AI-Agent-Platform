import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.control_plane.auth import validate_return_path
from app.control_plane.authorization import AuthorizationService
from app.control_plane.middleware import IdentitySecurityMiddleware, is_public_request


@pytest.mark.parametrize("path", [
    "/ai-notes",
    "/ai-notes/foundations/agent-engineering-learning-map",
])
def test_unauthenticated_ai_notes_shell_can_start_dingtalk_login(path):
    assert is_public_request("GET", path, "/")
    assert is_public_request("GET", "/_preview/dingtalk-r1" + path, "/_preview/dingtalk-r1/")
    assert validate_return_path(path, route_prefix="/") == path


@pytest.mark.parametrize("path", [
    "/api/v1/ai-notes",
    "/api/v1/ai-notes/foundations/agent-engineering-learning-map",
    "/ai-notes/foundations/agent-engineering-learning-map/extra",
    "/ai-notes/foundations/../admin",
])
def test_ai_notes_content_and_unknown_paths_still_require_authentication(path):
    assert not is_public_request("GET", path, "/")


def test_anonymous_deep_link_gets_login_shell_while_article_api_stays_private():
    class Auth:
        route_prefix = "/"
        cookie_name = "session"
        csrf_cookie_name = "csrf"
        public_base_url = "https://agent.orbbec.com.cn"
        trusted_proxy_networks = ()
        rate_limiter = None

        def authenticate(self, _token):
            return None

    app = FastAPI()

    @app.get("/ai-notes/{client_path:path}")
    def shell(client_path: str):
        return HTMLResponse('<meta name="platform-identity-mode" content="enabled">')

    @app.get("/api/v1/ai-notes/{category_slug}/{article_slug}")
    def article(category_slug: str, article_slug: str):
        return {"markdown": "private article"}

    app.add_middleware(IdentitySecurityMiddleware, auth=Auth(), public_assets=frozenset(),
                       authorization=AuthorizationService(None), routes=tuple(app.router.routes))
    client = TestClient(app)
    path = "/ai-notes/foundations/agent-engineering-learning-map"
    shell_response = client.get(path)
    article_response = client.get("/api/v1" + path)
    assert shell_response.status_code == 200
    assert "platform-identity-mode" in shell_response.text
    assert article_response.status_code == 401
    assert "private article" not in article_response.text
