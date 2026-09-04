# FAE Dual-Entry Management Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make both FAE URLs render the same FAE Agent experience while showing one secure `/fae/manage/` entry only to authorized internal enterprise users.

**Architecture:** Agent Platform owns a minimal same-origin navigation projection and the existing `/fae/manage/*` workbench. AI FAE Agent remains one application mounted at the public origin and the internal `/fae` base; its frontend conditionally renders the management link without changing the frozen FAE identity contract or sharing cookies across origins.

**Tech Stack:** FastAPI, Python 3.11+, React 19, TypeScript 5.9, Vitest, pytest, Nginx, Bash acceptance scripts

## Global Constraints

- Repositories: `/Users/neo/Developer/work/AI-Agent-Platform` and `/Users/neo/Developer/work/AI-FAE-Agent`.
- Work in isolated worktrees created at execution time; do not alter unrelated untracked files in either main checkout.
- `https://fae.orbbec.com.cn/` and `https://agent.orbbec.com.cn/fae/` use one FAE source tree, component tree, backend, and release version.
- Public customers and partner operators never receive or see the FAE management entry.
- The navigation endpoint returns only `management_workspace_url`; it returns no identity, PII, role, grant, token, or CSRF data.
- The only accepted non-null URL is the exact relative path `/fae/manage/`.
- Frontend visibility is not authorization; all `/fae/manage/*` pages and APIs retain server-side checks.
- Failure to load optional management navigation must never block FAE chat, attachments, or conversation history.
- Do not modify the FAE answer model, prompts, customer data policy, or management data model.
- Preserve normal document navigation between the FAE and Platform frontend bundles; do not use an iframe or SPA-cross-bundle routing.

---

## File Structure

### Agent Platform

- Modify `backend/app/control_plane/routes_auth.py`: expose the bounded FAE navigation projection.
- Modify `backend/tests/test_dingtalk_auth_api.py`: prove identity, grant, response-shape, and failure behavior.
- Modify `webui/src/components/fae-workbench/FaeWorkbenchShell.tsx`: make the reciprocal return action explicit.
- Modify `webui/src/components/fae-workbench/FaeWorkbenchShell.test.tsx`: lock the exact return link.
- Modify `deploy/cloud/accept.sh`: verify route ownership and the navigation projection with existing protected acceptance identities.
- Modify `backend/tests/test_cloud_deployment.py`: statically gate the production acceptance behavior.

### AI FAE Agent

- Create `webui/src/managementNavigation.ts`: validate and load the bounded Platform projection only on the internal surface.
- Create `webui/src/managementNavigation.test.ts`: lock origin, identity-mode, URL, and fail-closed behavior.
- Create `webui/src/FaeWorkspaceActions.tsx`: render the optional management affordance without owning authorization.
- Create `webui/src/FaeWorkspaceActions.test.tsx`: prove conditional rendering and failure isolation.
- Modify `webui/src/App.tsx`: mount the action in the existing direct-use workspace.
- Modify `webui/src/styles.css`: position the action unobtrusively at the top-right on desktop and mobile.
- Modify `webui/src/AppRender.test.tsx`: prove the FAE Agent remains the main page under both browser bases.
- Modify `deploy/scripts/verify_prod.sh`: verify the public FAE usage entry remains available.

---

### Task 1: Add the bounded Platform navigation projection

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `backend/app/control_plane/routes_auth.py`
- Test: `backend/tests/test_dingtalk_auth_api.py`

**Interfaces:**
- Consumes: `request.state.auth_context: AuthContext` and `request.app.state.fae_access.allows(context) -> bool`.
- Produces: `GET /api/v1/workspaces/fae/navigation -> {"management_workspace_url": "/fae/manage/" | null}`.

- [ ] **Step 1: Write failing authorization and response-shape tests**

Add tests that use the existing `FakeAuth`, `_app`, and FAE access doubles:

```python
def test_fae_navigation_projects_only_the_exact_management_url(tmp_path, monkeypatch):
    auth = FakeAuth()
    granted_user_id = uuid4()
    client = TestClient(_app(
        tmp_path, monkeypatch, auth,
        fae_access=_GrantingFaeAccess(granted_user_id),
    ))
    cookies = {auth.cookie_name: "valid-cookie"}

    auth.context = AuthContext(granted_user_id, Role.MEMBER, uuid4(), False)
    allowed = client.get("/api/v1/workspaces/fae/navigation", cookies=cookies)
    assert allowed.status_code == 200
    assert allowed.json() == {"management_workspace_url": "/fae/manage/"}
    assert allowed.headers["cache-control"] == "no-store"

    auth.context = AuthContext(uuid4(), Role.MEMBER, uuid4(), False)
    denied = client.get("/api/v1/workspaces/fae/navigation", cookies=cookies)
    assert denied.status_code == 200
    assert denied.json() == {"management_workspace_url": None}
    assert set(denied.json()) == {"management_workspace_url"}


def test_fae_navigation_requires_session_and_fails_closed_when_access_is_unavailable(
    tmp_path, monkeypatch,
):
    auth = FakeAuth()
    client = TestClient(_app(tmp_path, monkeypatch, auth, fae_access=_FailingFaeAccess()))
    assert client.get("/api/v1/workspaces/fae/navigation").status_code == 401
    unavailable = client.get(
        "/api/v1/workspaces/fae/navigation",
        cookies={auth.cookie_name: "valid-cookie"},
    )
    assert unavailable.status_code == 503
    assert unavailable.headers["cache-control"] == "no-store"
```

Reuse the existing `_FailingFaeAccess` test double, whose `allows` raises
`FaeWorkbenchAccessUnavailable`.

- [ ] **Step 2: Run the focused tests and verify the route is missing**

Run:

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_dingtalk_auth_api.py \
  -k 'fae_navigation' -q
```

Expected: FAIL with `404 Not Found` for the new endpoint.

- [ ] **Step 3: Implement the exact projection**

Add the route beside the account endpoints in `routes_auth.py`:

```python
@router.get("/api/v1/workspaces/fae/navigation")
async def fae_workspace_navigation(request: Request):
    context: AuthContext = request.state.auth_context
    fae_access = getattr(request.app.state, "fae_access", None)
    try:
        allowed = fae_access is not None and fae_access.allows(context)
    except FaeWorkbenchAccessUnavailable:
        raise HTTPException(503, "workspace navigation unavailable") from None
    return JSONResponse(
        {
            "management_workspace_url": (
                "/fae/manage/" if allowed else None
            )
        },
        headers={"Cache-Control": "no-store"},
    )
```

Use the existing imports for `AuthContext`, `FaeWorkbenchAccessUnavailable`, and
`HTTPException`; add `JSONResponse` to the existing `fastapi.responses` import.
Do not expose the account snapshot.

- [ ] **Step 4: Run focused and neighboring auth tests**

Run:

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_dingtalk_auth_api.py \
  backend/tests/test_r1_authorization.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the Platform endpoint**

```bash
git add backend/app/control_plane/routes_auth.py backend/tests/test_dingtalk_auth_api.py
git commit -m "feat(fae): expose bounded management navigation"
```

---

### Task 2: Add the internal-only FAE navigation client

**Repository:** `/Users/neo/Developer/work/AI-FAE-Agent`

**Files:**
- Create: `webui/src/managementNavigation.ts`
- Create: `webui/src/managementNavigation.test.ts`

**Interfaces:**
- Consumes: `AuthenticatedAccount | null`, `isInternalFaeSurface()`, and same-origin `fetch`.
- Produces: `loadFaeManagementWorkspaceUrl(account, fetcher?) -> Promise<"/fae/manage/" | null>`.

- [ ] **Step 1: Write the failing contract tests**

```typescript
import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadFaeManagementWorkspaceUrl } from './managementNavigation';

afterEach(() => {
  vi.unstubAllGlobals();
  history.replaceState(null, '', '/app/');
});

const enterprise = {
  mode: 'platform_enterprise' as const,
  displayName: '苍渊',
  partnerDisplayName: null,
};

describe('FAE management navigation projection', () => {
  it('loads only the exact internal management path', async () => {
    history.replaceState(null, '', '/fae/');
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      management_workspace_url: '/fae/manage/',
    }), { status: 200 }));
    await expect(loadFaeManagementWorkspaceUrl(enterprise, fetcher))
      .resolves.toBe('/fae/manage/');
    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/workspaces/fae/navigation',
      { credentials: 'include' },
    );
  });

  it.each([
    null,
    { ...enterprise, mode: 'platform_partner' as const },
  ])('never calls Platform for public or partner use', async (account) => {
    history.replaceState(null, '', '/app/');
    const fetcher = vi.fn();
    await expect(loadFaeManagementWorkspaceUrl(account, fetcher)).resolves.toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    { management_workspace_url: 'https://agent.orbbec.com.cn/fae/manage/' },
    { management_workspace_url: '/fae/manage/?source=chat' },
    { management_workspace_url: '/admin/fae' },
    { management_workspace_url: '/fae/manage/', extra: true },
  ])('rejects an unsafe or expanded projection', async (payload) => {
    history.replaceState(null, '', '/fae/');
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload)));
    await expect(loadFaeManagementWorkspaceUrl(enterprise, fetcher)).rejects.toThrow(
      'FAE management navigation response invalid',
    );
  });
});
```

- [ ] **Step 2: Run the test and verify the module is missing**

Run:

```bash
npm --prefix webui test -- managementNavigation.test.ts
```

Expected: FAIL because `managementNavigation.ts` does not exist.

- [ ] **Step 3: Implement the strict loader**

```typescript
import type { AuthenticatedAccount } from './enterpriseIdentity';
import { isInternalFaeSurface } from './runtimePaths';

export type FaeManagementWorkspaceUrl = '/fae/manage/';

export async function loadFaeManagementWorkspaceUrl(
  account: AuthenticatedAccount | null,
  fetcher: typeof fetch = fetch,
): Promise<FaeManagementWorkspaceUrl | null> {
  if (!isInternalFaeSurface() || account?.mode !== 'platform_enterprise') return null;
  const response = await fetcher('/api/v1/workspaces/fae/navigation', {
    credentials: 'include',
  });
  if (!response.ok) throw new Error('FAE management navigation unavailable');
  const value: unknown = await response.json();
  if (
    value === null || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== 1
    || !Object.hasOwn(value, 'management_workspace_url')
  ) throw new Error('FAE management navigation response invalid');
  const url = (value as { management_workspace_url: unknown }).management_workspace_url;
  if (url === null) return null;
  if (url !== '/fae/manage/') {
    throw new Error('FAE management navigation response invalid');
  }
  return url;
}
```

- [ ] **Step 4: Run the focused test**

```bash
npm --prefix webui test -- managementNavigation.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit the FAE client contract**

```bash
git add webui/src/managementNavigation.ts webui/src/managementNavigation.test.ts
git commit -m "feat(webui): load internal FAE management navigation"
```

---

### Task 3: Render the optional management action without blocking FAE Agent use

**Repository:** `/Users/neo/Developer/work/AI-FAE-Agent`

**Files:**
- Create: `webui/src/FaeWorkspaceActions.tsx`
- Create: `webui/src/FaeWorkspaceActions.test.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/styles.css`
- Test: `webui/src/AppRender.test.tsx`

**Interfaces:**
- Consumes: `loadFaeManagementWorkspaceUrl(account) -> Promise<"/fae/manage/" | null>`.
- Produces: `<FaeWorkspaceActions account={account} />`, rendering zero or one exact management link.

- [ ] **Step 1: Write failing component tests**

Create tests that inject the loader so no identity globals are forged:

```tsx
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FaeWorkspaceActions } from './FaeWorkspaceActions';

const enterprise = {
  mode: 'platform_enterprise' as const,
  displayName: '苍渊',
  partnerDisplayName: null,
};

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  history.replaceState(null, '', '/app/');
});

it('shows one quiet management action for an authorized internal account', async () => {
  history.replaceState(null, '', '/fae/');
  const load = vi.fn().mockResolvedValue('/fae/manage/' as const);
  await act(async () => root.render(
    <FaeWorkspaceActions account={enterprise} load={load} />,
  ));
  expect(container.querySelectorAll("a[href='/fae/manage/']")).toHaveLength(1);
  expect(container.textContent).toContain('管理工作台');
});

it('keeps the Agent usable and hides the optional action when projection fails', async () => {
  history.replaceState(null, '', '/fae/');
  const load = vi.fn().mockRejectedValue(new Error('unavailable'));
  await act(async () => root.render(
    <main><p>FAE 对话</p><FaeWorkspaceActions account={enterprise} load={load} /></main>,
  ));
  expect(container.textContent).toContain('FAE 对话');
  expect(container.textContent).not.toContain('管理工作台');
  expect(container.querySelector('[role="alert"]')).toBeNull();
});
```

Also add an `AppRender.test.tsx` assertion that `/app/` and `/fae/` both render `AI FAE 技术咨询`, and that neither route renders Platform management content.

- [ ] **Step 2: Run the focused tests and verify failure**

```bash
npm --prefix webui test -- FaeWorkspaceActions.test.tsx AppRender.test.tsx
```

Expected: FAIL because the component and management action do not exist.

- [ ] **Step 3: Implement the isolated component**

```tsx
import { useEffect, useState } from 'react';
import type { AuthenticatedAccount } from './enterpriseIdentity';
import {
  loadFaeManagementWorkspaceUrl,
  type FaeManagementWorkspaceUrl,
} from './managementNavigation';

export function FaeWorkspaceActions({
  account,
  load = loadFaeManagementWorkspaceUrl,
}: {
  account: AuthenticatedAccount | null;
  load?: typeof loadFaeManagementWorkspaceUrl;
}) {
  const [url, setUrl] = useState<FaeManagementWorkspaceUrl | null>(null);
  useEffect(() => {
    let active = true;
    setUrl(null);
    void load(account).then((value) => {
      if (active) setUrl(value);
    }).catch(() => {
      if (active) setUrl(null);
    });
    return () => { active = false; };
  }, [account, load]);
  if (!url) return null;
  return <nav className="fae-workspace-actions" aria-label="FAE 工作区">
    <a href={url}>管理工作台</a>
  </nav>;
}
```

Mount it as the first child of `.chat-workspace` in `App.tsx`:

```tsx
<section className="chat-workspace">
  <FaeWorkspaceActions account={account} />
  <div className="message-list" aria-live="polite">
```

Add compact, non-overlaying desktop/mobile styles. Keep the link above content in normal flow on narrow screens:

```css
.fae-workspace-actions {
  display: flex;
  justify-content: flex-end;
  padding: 14px 24px 0;
}

.fae-workspace-actions a {
  color: #2f6b45;
  font-size: 13px;
  font-weight: 700;
  text-decoration: none;
}
```

- [ ] **Step 4: Run FAE WebUI tests and build**

```bash
npm --prefix webui test
npm --prefix webui run build
```

Expected: all tests PASS and the production build exits 0.

- [ ] **Step 5: Commit the FAE affordance**

```bash
git add webui/src/App.tsx webui/src/styles.css \
  webui/src/FaeWorkspaceActions.tsx webui/src/FaeWorkspaceActions.test.tsx \
  webui/src/AppRender.test.tsx
git commit -m "feat(webui): link authorized users to FAE management"
```

---

### Task 4: Make management-to-Agent navigation explicit

**Repository:** `/Users/neo/Developer/work/AI-Agent-Platform`

**Files:**
- Modify: `webui/src/components/fae-workbench/FaeWorkbenchShell.tsx`
- Test: `webui/src/components/fae-workbench/FaeWorkbenchShell.test.tsx`

**Interfaces:**
- Consumes: `FAE_DIRECT_PATH === "/fae/"` and `platformPath()`.
- Produces: one normal document link labeled `返回 FAE Agent`.

- [ ] **Step 1: Change the existing test expectation first**

```typescript
expect([...container.querySelectorAll<HTMLAnchorElement>(
  '.fae-workbench__workspace-nav a',
)].map((link) => [link.textContent, link.getAttribute('href')])).toEqual([
  ['返回 FAE Agent', '/fae/'],
  ['管理', '/fae/manage/'],
]);
```

- [ ] **Step 2: Run the focused test and verify copy mismatch**

```bash
npm --prefix webui test -- FaeWorkbenchShell.test.tsx
```

Expected: FAIL because the existing label is `Agent`.

- [ ] **Step 3: Change only the reciprocal label**

```tsx
<a href={platformPath(FAE_DIRECT_PATH)}>返回 FAE Agent</a>
```

Do not convert this cross-bundle navigation to `PlatformLink`.

- [ ] **Step 4: Run Platform WebUI tests and build**

```bash
npm --prefix webui test
npm --prefix webui run build
```

Expected: all tests PASS and the production build exits 0.

- [ ] **Step 5: Commit the reciprocal navigation**

```bash
git add webui/src/components/fae-workbench/FaeWorkbenchShell.tsx \
  webui/src/components/fae-workbench/FaeWorkbenchShell.test.tsx
git commit -m "fix(fae): make Agent return navigation explicit"
```

---

### Task 5: Add cross-repository release gates and perform acceptance

**Repositories:** both repositories

**Files:**
- Modify: `AI-Agent-Platform/deploy/cloud/accept.sh`
- Modify: `AI-Agent-Platform/backend/tests/test_cloud_deployment.py`
- Modify: `AI-FAE-Agent/deploy/scripts/verify_prod.sh`
- Test: `AI-FAE-Agent/tests/unit/test_verify_prod_script.py`
- Test: existing Platform deployment tests

**Interfaces:**
- Consumes: the new navigation endpoint and existing explicit Nginx owners.
- Produces: release evidence that both usage entries work and management stays internal.

- [ ] **Step 1: Write failing static deployment assertions**

In Platform deployment tests, require acceptance to probe the new endpoint with the existing owner and ordinary-member cookie jars without printing their values:

```python
assert '"$base/api/v1/workspaces/fae/navigation"' in script
assert '"management_workspace_url":"/fae/manage/"' in script
assert '"management_workspace_url":null' in script
```

In the FAE deploy-script tests, require both the existing `/app/` route and the configured public root to be bounded GETs.

- [ ] **Step 2: Run deployment tests and verify the missing gates**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
backend/.venv/bin/python -m pytest backend/tests/test_cloud_deployment.py -q

cd /Users/neo/Developer/work/AI-FAE-Agent
python -m pytest tests/unit/test_verify_prod_script.py -q
```

Expected: FAIL on the newly added assertions.

- [ ] **Step 3: Implement safe acceptance probes**

Use existing cookie arrays/files and `jq`; never interpolate cookies into command output:

```bash
owner_navigation="$(${curl_owner[@]} -fsS \
  "$base/api/v1/workspaces/fae/navigation")" || fail
[[ "$(printf '%s' "$owner_navigation" | jq -r '.management_workspace_url')" \
  == '/fae/manage/' ]] || fail

member_navigation="$(${curl_member[@]} -fsS \
  "$base/api/v1/workspaces/fae/navigation")" || fail
[[ "$(printf '%s' "$member_navigation" | jq -r '.management_workspace_url')" \
  == 'null' ]] || fail
```

Keep existing route-owner assertions for `location ^~ /fae/manage/` before
`location ^~ /fae/`. In FAE `verify_prod.sh`, add a bounded GET of the configured
public root while retaining `/app/` for compatibility:

```bash
bounded_get "${PUBLIC_BASE_URL%/}/"
verify_passed public_agent_home
```

- [ ] **Step 4: Run complete local verification**

```bash
cd /Users/neo/Developer/work/AI-Agent-Platform
backend/.venv/bin/python -m pytest
npm --prefix webui test
npm --prefix webui run build
bash -n deploy/cloud/accept.sh

cd /Users/neo/Developer/work/AI-FAE-Agent
python -m pytest
npm --prefix webui test
npm --prefix webui run build
bash -n deploy/scripts/verify_prod.sh
```

Expected: all suites PASS; both builds and shell syntax checks exit 0.

- [ ] **Step 5: Commit each repository's release gates separately**

Platform:

```bash
git add deploy/cloud/accept.sh backend/tests/test_cloud_deployment.py
git commit -m "test(fae): gate dual-entry navigation release"
```

FAE:

```bash
git add deploy/scripts/verify_prod.sh tests/unit/test_verify_prod_script.py
git commit -m "test(deploy): verify public FAE Agent home"
```

- [ ] **Step 6: Release in dependency order and run real acceptance**

1. Deploy Agent Platform first so the optional endpoint exists.
2. Verify current FAE chat and `/fae/manage/` before changing FAE.
3. Deploy AI FAE Agent.
4. With an external or partner identity, verify `fae.orbbec.com.cn/` shows the
   FAE Agent and no management link.
5. With an internal ordinary member, verify `/fae/` shows the FAE Agent and no
   management link.
6. With `苍渊` or another active FAE workbench manager, verify `/fae/` shows one
   `管理工作台` link and that it opens `/fae/manage/`.
7. Verify chat streaming, attachments, conversation history, direct refresh,
   mobile layout, and `返回 FAE Agent`.
8. Revoke a test grant, refresh, and verify the link disappears and direct
   management access returns 403.

Record only status, release SHAs, and boolean assertions. Do not record cookies,
launch codes, customer prompts, attachment names, or response bodies.
