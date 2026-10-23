"""Exercise the delivered browser script, retaining HTTP identity/CSRF boundaries."""
from __future__ import annotations

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.control_plane.middleware import is_public_request
from app.control_plane.models import IdentityMode
from test_dingtalk_auth_api import FakeAuth, _app, _FaeLaunch

SHELL = "/agents/ai-fae-agent"
SCRIPT = "/api/v1/auth/fae-launch.js"
LAUNCH = "/api/v1/agents/ai-fae-agent/launch"


class Scripts(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.sources = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.sources.append(dict(attrs).get("src"))
        assert not any(key.startswith("on") for key, _ in attrs)


@pytest.mark.parametrize("prefix", ["/", "/_preview/dingtalk-r1/"])
def test_exact_static_public_handoff_and_protected_neighbors(tmp_path, monkeypatch, prefix):
    auth = FakeAuth(prefix=prefix, mode=IdentityMode.PREVIEW if prefix != "/" else IdentityMode.PRODUCTION)
    service = _FaeLaunch()
    client = TestClient(_app(tmp_path, monkeypatch, auth, agent_launch_service=service))
    base = prefix.rstrip("/")
    for path in (SHELL, SCRIPT):
        response = client.get(base + path + "?return_path=https://evil.test/secret&code=SECRET", headers={"Origin": "https://evil.test"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "SECRET" not in response.text
        assert "evil.test" not in response.text
        assert "access-control-allow-origin" not in response.headers
        for method in ("HEAD", "POST", "PUT", "OPTIONS"):
            assert not is_public_request(method, base + path, prefix)
        assert is_public_request("GET", base + path, prefix)
    page = client.get(base + SHELL)
    assert Scripts(page.text).sources == [base + SCRIPT]
    assert "LOGIN SHELL" not in page.text  # exact route precedes generic SPA
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert "connect-src 'self'" in page.headers["content-security-policy"]
    assert "unsafe-inline" not in page.headers["content-security-policy"]
    assert client.get(base + SCRIPT).headers["content-type"].startswith("application/javascript")
    for path in ("/api/v1/account", LAUNCH, SHELL + "/", SHELL + "/debug", "/agents/other-agent", SCRIPT + "/", SCRIPT + ".map"):
        assert client.get(base + path, follow_redirects=False).status_code == 401
    assert client.post(base + LAUNCH).status_code == 401
    assert client.post(base + LAUNCH, cookies={auth.cookie_name: "valid-cookie"}, headers={"Origin": auth.public_base_url}).status_code == 403
    assert service.calls == []  # shell/asset GET and rejected mutations mint nothing
    for alias in ("/%61gents/ai-fae-agent", "/api/v1/auth/fae-launch%2Ejs"):
        assert client.get(base + alias, follow_redirects=False).status_code == 401


# Run the exact HTTP response body, with only browser APIs and HTTP transport emulated.
NODE_BROWSER = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
(async () => {
  const requests = [], navigations = [], logs = [];
  const status = {textContent: ''};
  const retry = {hidden: true, disabled: false, addEventListener(type, fn) { this.click = fn; }};
  const queue = [...input.responses];
  const sandbox = {
    document: {getElementById(id) { return id === 'fae-launch-status' ? status : retry; }},
    window: {location: {pathname: input.pathname, replace(url) { navigations.push(url); }}},
    fetch: async (url, options) => {
      requests.push({url, options});
      assert(queue.length > 0, 'unexpected automatic request/retry');
      const reply = queue.shift();
      if (reply.networkError) throw new Error('PRIVATE_ERROR');
      return {status: reply.status, ok: reply.status >= 200 && reply.status < 300,
        json: async () => { if (reply.invalidJson) throw new Error('PRIVATE_ERROR'); return reply.body; }};
    },
    console: {log(...x) {logs.push(x);}, error(...x) {logs.push(x);}},
    URL, encodeURIComponent,
  };
  vm.runInNewContext(input.script, sandbox, {timeout: 1000});
  const settle = async () => { for (let i = 0; i < 10; i++) await new Promise(setImmediate); };
  await settle();
  const initial = {requests: requests.length, hidden: retry.hidden, message: status.textContent};
  if (input.retry) { assert.equal(retry.hidden, false); await retry.click(); await settle(); }
  let loginConsumer;
  if (input.consumeLogin) {
    // Bundle the actual consumer in memory: no duplicated return-path allowlist.
    const esbuild = require(require.resolve('esbuild', {paths: [input.webui]}));
    const source = esbuild.buildSync({entryPoints: [input.webui + '/src/auth.ts'],
      bundle: true, format: 'cjs', platform: 'node', write: false}).outputFiles[0].text;
    const loginUrl = new URL(navigations[0], 'https://agent.example.test');
    const qrRequests = [];
    const consumerModule = {exports: {}};
    const consumerContext = {
      module: consumerModule, URL, URLSearchParams,
      window: {location: {pathname: loginUrl.pathname}},
      fetch: async (url, options) => {
        qrRequests.push({url, options});
        if (url.endsWith('/api/v1/account')) return {status: 401, ok: false};
        return {status: 200, ok: true,
          json: async () => ({authorization_url: 'https://login.dingtalk.com/test'})};
      },
    };
    vm.runInNewContext(source, consumerContext, {timeout: 1000});
    const consumer = consumerModule.exports;
    const selected = consumer.loginReturnPath(loginUrl.search);
    const successTarget = consumer.platformPath(selected, consumer.routePrefix(loginUrl.pathname));
    await consumer.startQrLogin(selected);
    loginConsumer = {selected, successTarget, qrRequests};
  }
  process.stdout.write(JSON.stringify({requests, navigations, logs, message: status.textContent, hidden: retry.hidden, initial, loginConsumer}));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    auth = FakeAuth()
    service = _FaeLaunch()
    client = TestClient(_app(tmp_path, monkeypatch, auth, agent_launch_service=service))
    response = client.get(SCRIPT)
    assert response.status_code == 200
    return client, auth, service, response.text


def browser(script, responses, *, prefix="", retry=False, pathname=None, consume_login=False):
    result = subprocess.run(["node", "-e", NODE_BROWSER], input=json.dumps({"script": script, "responses": responses, "pathname": pathname or prefix + SHELL, "retry": retry, "consumeLogin": consume_login, "webui": str(Path(__file__).resolve().parents[2] / "webui")}), text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_script_consumes_real_account_csrf_and_launch_response(handoff):
    client, auth, service, script = handoff
    cookies = {auth.cookie_name: "valid-cookie", auth.csrf_cookie_name: auth.csrf}
    account = client.get("/api/v1/account", cookies=cookies)
    launch = client.post(LAUNCH, cookies=cookies, headers={"Origin": auth.public_base_url, "X-CSRF-Token": account.json()["csrf_token"]})
    assert account.status_code == launch.status_code == 200
    result = browser(script, [{"status": 200, "body": account.json()}, {"status": 200, "body": launch.json()}])
    assert result["navigations"] == [launch.json()["launch_url"]]
    assert [r["url"] for r in result["requests"]] == ["/api/v1/account", LAUNCH]
    assert result["requests"][1]["options"]["method"] == "POST"
    assert result["requests"][1]["options"]["headers"] == {"X-CSRF-Token": auth.csrf}
    assert "body" not in result["requests"][1]["options"]
    for request in result["requests"]:
        assert request["options"]["credentials"] == "same-origin"
        assert request["options"]["cache"] == "no-store"
        assert request["options"]["redirect"] == "error"
    assert result["logs"] == []
    assert auth.csrf not in result["message"]
    assert launch.json()["launch_url"] not in result["message"]
    assert len(service.calls) == 1


@pytest.mark.parametrize("prefix", ["", "/_preview/dingtalk-r1"])
def test_script_401_replaces_document_with_fixed_prefixed_login(handoff, prefix):
    *_, script = handoff
    result = browser(script, [{"status": 401}], prefix=prefix, consume_login=True)
    assert result["loginConsumer"]["selected"] == SHELL
    assert result["loginConsumer"]["successTarget"] == prefix + SHELL
    qr_requests = result["loginConsumer"]["qrRequests"]
    assert [request["url"] for request in qr_requests] == [prefix + "/login", prefix + "/api/v1/account", prefix + "/api/v1/auth/dingtalk/start"]
    assert json.loads(qr_requests[-1]["options"]["body"]) == {"return_path": prefix + SHELL}
    from urllib.parse import quote
    assert result["navigations"] == [prefix + "/login?return_path=" + quote(SHELL, safe="")]
    assert [r["url"] for r in result["requests"]] == [prefix + "/api/v1/account"]


ACCOUNT = {"status": 200, "body": {"csrf_token": "real-csrf", "mobile": "PRIVATE_DATA"}}
VALID_URL = "https://fae.orbbec.com.cn/#platform_launch=" + "x" * 43


@pytest.mark.parametrize("url", [
    "https://evil.test/#platform_launch=" + "x" * 43,
    VALID_URL.replace("https:", "http:"), VALID_URL.replace(".cn/", ".cn:443/"),
    VALID_URL.replace(".cn/", ".cn/other"), VALID_URL.replace(".cn/", ".cn/?x=1"),
    VALID_URL.replace("https://", "https://user@"), VALID_URL.replace("fae.", "FAE."),
    VALID_URL + "&next=evil", VALID_URL + "\n", VALID_URL.replace("x" * 43, "x" * 31),
    VALID_URL.replace("x" * 43, "x" * 257), VALID_URL.replace("x" * 43, "%78" * 43),
    "//fae.orbbec.com.cn/#platform_launch=" + "x" * 43, None, 123,
])
def test_script_rejects_noncanonical_launch_urls_without_echo_or_fallback(handoff, url):
    *_, script = handoff
    result = browser(script, [ACCOUNT, {"status": 200, "body": {"launch_url": url}}])
    assert result["navigations"] == []
    assert len(result["requests"]) == 2
    assert result["hidden"] is False
    assert "重试" in result["message"]
    assert "PRIVATE" not in result["message"]
    assert "platform_launch" not in result["message"]
    assert result["logs"] == []


@pytest.mark.parametrize("responses,permission", [
    ([{"status": 403}], True), ([ACCOUNT, {"status": 403}], True),
    ([{"status": 503}], False), ([{"networkError": True}], False),
    ([{"status": 200, "invalidJson": True}], False),
    ([{"status": 200, "body": None}], False),
    ([{"status": 200, "body": {"csrf_token": ""}}], False),
    ([{"status": 200, "body": {"csrf_token": "real-csrf\n"}}], False),
    ([{"status": 200, "body": {"csrf_token": 123}}], False),
    ([ACCOUNT, {"status": 200, "invalidJson": True}], False),
    ([ACCOUNT, {"status": 200, "body": None}], False),
    ([ACCOUNT, {"status": 401}], False),
    ([ACCOUNT, {"status": 503, "body": {"detail": "PRIVATE_ERROR"}}], False),
    ([ACCOUNT, {"networkError": True}], False),
])
def test_script_errors_are_explicit_manual_retry_only(handoff, responses, permission):
    *_, script = handoff
    result = browser(script, responses)
    assert result["navigations"] == []
    assert len(result["requests"]) == len(responses)
    assert result["hidden"] is False
    assert ("权限" in result["message"]) is permission
    assert "重试" in result["message"]
    assert "PRIVATE" not in result["message"]
    assert result["logs"] == []


def test_script_manual_retry_starts_fresh_without_replaying_post_automatically(handoff):
    *_, script = handoff
    result = browser(script, [ACCOUNT, {"networkError": True}, ACCOUNT, {"status": 200, "body": {"launch_url": VALID_URL}}], retry=True)
    assert result["initial"]["requests"] == 2
    assert result["initial"]["hidden"] is False
    assert result["navigations"] == [VALID_URL]
    assert len(result["requests"]) == 4


def test_script_fails_closed_outside_exact_handoff_path(handoff):
    *_, script = handoff
    for path in ("/agents/other-agent", SHELL + "/", "/../" + SHELL.lstrip("/")):
        result = browser(script, [], pathname=path)
        assert result["requests"] == result["navigations"] == []
        assert result["message"]


@pytest.mark.parametrize("code", ["A_" * 16, "-9z_" * 64])
def test_script_accepts_full_urlsafe_code_range_and_preserves_prefix(handoff, code):
    *_, script = handoff
    url = "https://fae.orbbec.com.cn/#platform_launch=" + code
    prefix = "/_preview/dingtalk-r1"
    result = browser(script, [ACCOUNT, {"status": 200, "body": {"launch_url": url}}], prefix=prefix)
    assert result["navigations"] == [url]
    assert [r["url"] for r in result["requests"]] == [prefix + "/api/v1/account", prefix + LAUNCH]


def test_unsupported_deployment_prefix_fails_closed_without_script(tmp_path, monkeypatch):
    auth = FakeAuth(prefix="/unsafe.prefix/")
    client = TestClient(_app(tmp_path, monkeypatch, auth))
    response = client.get("/unsafe.prefix" + SHELL)
    assert response.status_code == 503
    assert Scripts(response.text).sources == []
