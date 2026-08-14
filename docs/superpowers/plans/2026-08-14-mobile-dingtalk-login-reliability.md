# Mobile DingTalk Login Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the DingTalk workbench entry use stable in-client login and recover browser users from duplicate OAuth callbacks without exposing raw error responses.

**Architecture:** Bundle the pinned official DingTalk JSAPI into the existing hashed frontend asset and use its environment plus `requestAuthCode` API instead of a timing-sensitive `window.dd` global. Keep the current backend in-client exchange and QR login boundaries, and add a callback recovery branch that only trusts a server-validated Platform Session.

**Tech Stack:** React 19, TypeScript, Vite, Vitest, `dingtalk-jsapi==3.2.9`, FastAPI, Pytest, Docker Compose, Nginx.

## Global Constraints

- Do not change DingTalk identity mapping, directory membership, roles, authorization, Session lifetimes, CSRF, replica data, FAE, ADMIN, or public listener topology.
- Keep the production Content Security Policy (CSP) self-only; the DingTalk SDK must be bundled and never loaded from a remote script origin.
- Do not persist provider identifiers, authorization codes, AppSecret values, user tokens, Platform Session tokens, or CSRF tokens in browser storage.
- Preserve QR login for ordinary browsers.
- Keep OAuth state single-use; callback recovery may only trust an independently authenticated Platform Session.
- Every production callback response remains generic and non-cacheable.

---

### Task 1: Stable Bundled DingTalk In-Client Login

**Files:**
- Modify: `webui/package.json`
- Modify: `webui/package-lock.json`
- Modify: `webui/src/auth.ts`
- Modify: `webui/src/auth.test.ts`
- Modify: `webui/src/pages/LoginPage.tsx`
- Modify: `webui/src/pages/LoginPage.test.tsx`

**Interfaces:**
- Consumes: `dd.env.platform: string` and `dd.requestAuthCode({clientId, corpId}): Promise<{code: string}>` from `dingtalk-jsapi@3.2.9`.
- Produces: `inClientLoginAvailable(): boolean`, `inClientLogin(): Promise<void>`, and a login page that attempts in-client login at most once per mount while retaining manual in-client and QR controls.

- [ ] **Step 1: Add failing SDK-boundary tests**

In `webui/src/auth.test.ts`, mock the package before importing `auth.ts`, then prove that DingTalk availability comes from `dd.env.platform`, not `window.dd`, and that `requestAuthCode` receives both public identifiers:

```typescript
const { requestAuthCode, dingTalkSdk } = vi.hoisted(() => {
  const requestAuthCode = vi.fn();
  return {
    requestAuthCode,
    dingTalkSdk: { env: { platform: "android" }, requestAuthCode },
  };
});
vi.mock("dingtalk-jsapi", () => ({ default: dingTalkSdk }));

it("detects the bundled DingTalk runtime without window.dd", () => {
  delete (window as typeof window & { dd?: unknown }).dd;
  expect(inClientLoginAvailable()).toBe(true);
});

it("uses the bundled DingTalk SDK for the one-time code", async () => {
  requestAuthCode.mockResolvedValueOnce({ code: "one-time-code" });
  await inClientLogin();
  expect(requestAuthCode).toHaveBeenCalledWith({
    clientId: "client",
    corpId: "corp",
  });
});
```

In `webui/src/pages/LoginPage.test.tsx`, add one test that renders with a successful `onInClient`, waits for the effect, and asserts exactly one call plus navigation to `/account`; retain the manual button assertion after a rejected automatic call.

- [ ] **Step 2: Run the focused frontend tests and verify RED**

Run:

```bash
cd webui
npm test -- --run src/auth.test.ts src/pages/LoginPage.test.tsx
```

Expected: FAIL because `dingtalk-jsapi` is not installed, availability still reads `window.dd`, and `LoginPage` does not automatically invoke in-client login.

- [ ] **Step 3: Pin the official SDK**

Run:

```bash
cd webui
npm install --save-exact dingtalk-jsapi@3.2.9
```

Expected: `package.json` contains exact version `3.2.9` and `package-lock.json` records the registry integrity.

- [ ] **Step 4: Implement the minimal SDK adapter**

In `webui/src/auth.ts`, import the package and replace the global bridge boundary:

```typescript
import dd from "dingtalk-jsapi";

export function inClientLoginAvailable(): boolean {
  return dd.env.platform !== "notInDingTalk";
}

export async function inClientLogin(): Promise<void> {
  if (!inClientLoginAvailable()) throw new Error("DingTalk JSAPI unavailable");
  const config = await loadPublicDingTalkConfig();
  const result = await dd.requestAuthCode({
    clientId: config.client_id,
    corpId: config.corp_id,
  });
  if (!result || typeof result.code !== "string" || !result.code) {
    throw new Error("DingTalk authorization failed");
  }
  await exchangeInClientCode(result.code);
}
```

Keep the existing strict config response validation inside the extracted `loadPublicDingTalkConfig()` helper.

In `LoginPage.tsx`, use a `useRef(false)` guard and `useEffect` to call `beginInClient()` once when an in-client action exists. A rejected call must clear `busy`, show the generic error, and keep both retry buttons usable.

- [ ] **Step 5: Run focused and complete frontend verification**

Run:

```bash
cd webui
npm test -- --run src/auth.test.ts src/pages/LoginPage.test.tsx
npm test -- --run
npm run build
```

Expected: all Vitest tests pass and Vite produces one hashed application script containing the bundled SDK with no remote DingTalk script tag.

- [ ] **Step 6: Commit the frontend fix**

```bash
git add webui/package.json webui/package-lock.json webui/src/auth.ts webui/src/auth.test.ts webui/src/pages/LoginPage.tsx webui/src/pages/LoginPage.test.tsx
git commit -m "fix(identity): stabilize DingTalk in-client login"
```

---

### Task 2: Recover Duplicate Browser Callbacks

**Files:**
- Modify: `backend/app/control_plane/routes_auth.py`
- Modify: `backend/tests/test_dingtalk_auth_api.py`

**Interfaces:**
- Consumes: `auth.authenticate(cookie_token)` and existing `_local_path(auth, path)`.
- Produces: authenticated duplicate callback redirect to `/account`; invalid unauthenticated callback redirect to `/login?error=1`; existing successful callback and rate-limit behavior remain unchanged.

- [ ] **Step 1: Write failing callback tests**

Replace the raw-JSON callback failure expectation with a generic login redirect, and add authenticated duplicate recovery:

```python
def test_invalid_callback_redirects_to_generic_login_error(tmp_path, monkeypatch):
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

def test_authenticated_duplicate_callback_recovers_to_account(tmp_path, monkeypatch):
    auth = FakeAuth()
    response = TestClient(_app(tmp_path, monkeypatch, auth)).get(
        "/api/v1/auth/dingtalk/callback?state=replayed&code=replayed",
        cookies={auth.cookie_name: "valid-cookie"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/account"
    assert auth.provider_calls == 0
```

- [ ] **Step 2: Run the backend callback tests and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_dingtalk_auth_api.py -q
```

Expected: the invalid callback still returns 401 JSON and the authenticated duplicate still calls the provider path.

- [ ] **Step 3: Implement callback recovery**

At the start of the callback route in `routes_auth.py`, validate an existing Platform Session Cookie and redirect authenticated callers to `_local_path(auth, "/account")`. Convert only `AuthenticationError` callback failures to a 302 redirect to `_local_path(auth, "/login") + "?error=1"`. Keep rate-limit and dependency failures on their current status codes.

- [ ] **Step 4: Run focused and complete backend verification**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_dingtalk_auth_api.py tests/test_web_session_security.py -q
.venv/bin/python -m pytest -q
```

Expected: all Pytest tests pass.

- [ ] **Step 5: Commit the backend fix**

```bash
git add backend/app/control_plane/routes_auth.py backend/tests/test_dingtalk_auth_api.py
git commit -m "fix(identity): recover duplicate DingTalk callbacks"
```

---

### Task 3: Release and Production Verification

**Files:**
- Verify: `deploy/cloud/compose.yaml`
- Verify: `deploy/cloud/accept-dingtalk-production.sh`
- Verify: `docs/runbooks/cloud-platform.md`

**Interfaces:**
- Consumes: clean reviewed commit reachable as both local `HEAD` and `origin/master`, plus the existing mode-0600 private deploy configuration.
- Produces: immutable production release, preserved FAE identity and listeners, healthy DingTalk services, and a real `in_client` login attempt.

- [ ] **Step 1: Run repository release gates**

Run:

```bash
git diff --check
cd webui && npm test -- --run && npm run build
cd ../backend && .venv/bin/python -m pytest -q
cd .. && deploy/cloud/acceptance.sh local
```

Expected: every command exits 0.

- [ ] **Step 2: Push the reviewed commits to the feature branch and master**

Run from the repository root:

```bash
git push origin HEAD:feat/agent-public-entry
git push origin HEAD:master
```

Expected: both remote references point to the same reviewed release commit.

- [ ] **Step 3: Deploy the immutable release**

Run:

```bash
deploy/cloud/deploy.sh "/Users/neo/Library/Application Support/OrbbecAI-Agent-Platform/cloud-replica/deploy.env"
```

Expected: `CLOUD_PLATFORM_DEPLOY_OK release=<current-commit> mode=dingtalk` without changing the FAE container identity, start time, image, or public listener set.

- [ ] **Step 4: Re-apply and verify the production identity boundary**

Run remotely through the existing key:

```bash
ssh -i /Users/neo/.ssh/orbbec_aliyun_ed25519 -o BatchMode=yes -o IdentitiesOnly=yes root@47.106.112.69 \
  '/opt/orbbec-agent-platform/current/deploy/cloud/publish-dingtalk-production.sh /opt/orbbec-agent-platform/current && /opt/orbbec-agent-platform/current/deploy/cloud/accept-dingtalk-production.sh'
```

Expected: the production acceptance script exits 0, all five Platform services are healthy, port 8080 remains loopback-only, and FAE is unchanged.

- [ ] **Step 5: Verify public browser behavior**

Run:

```bash
curl --noproxy '*' -sS -D - -o /dev/null https://agent.orbbec.com.cn/
curl --noproxy '*' -sS https://agent.orbbec.com.cn/login
```

Expected: root redirects to `/login`; login returns the first-party shell; the HTML contains no remote script origin; QR login remains visible in an ordinary browser.

- [ ] **Step 6: Perform the real mobile acceptance**

Open the Agent-Platform workbench entry in DingTalk mobile. Expected: the page automatically completes in-client login and opens `/account` without displaying a callback JSON error. Then query only aggregate production evidence and require at least one recent successful `in_client` attempt.

- [ ] **Step 7: Record the verified outcome**

Report the release commit, frontend/backend test counts, production acceptance result, real mobile result, and whether any manual DingTalk developer-console change remains. Do not report provider identifiers, codes, tokens, Cookie values, or secrets.
