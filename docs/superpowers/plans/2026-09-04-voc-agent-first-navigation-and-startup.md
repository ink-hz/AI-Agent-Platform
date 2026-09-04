# VOC Agent-First Navigation and Startup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/voc/` the durable VOC Agent usage homepage, expose `/voc/manage/` as one capability-gated secondary entry, and replace the generic startup failure with recoverable identity-specific states.

**Architecture:** The standalone VOC application remains the route owner for both direct use and management. Its existing `/voc/session` projection remains the only frontend capability source; the WebUI separates identity bootstrap state from route permission state so a management or identity failure cannot erase the Agent-first navigation model.

**Tech Stack:** FastAPI, Python 3.11+, React 19, TypeScript 5.6, Vitest 3.2, pytest, Docker Compose, production smoke scripts

## Global Constraints

- Repository: `/Users/neo/Developer/work/Orbbec-VOC-Agent`.
- Work in an isolated worktree created at execution time; do not modify other repositories or unrelated worktree changes.
- `/voc/` is always the VOC Agent direct-use root.
- `/voc/manage/` is the only canonical management root; `?view=management` remains compatibility-only.
- Ordinary users never see `管理工作台`; authorized users see exactly one compact entry.
- Direct unauthorized management access remains 403 and includes a safe return to `/voc/`.
- A 401 redirects to Platform login using the existing safe return-path rules.
- A transient identity failure offers an in-place retry; it does not rewrite the route or discard browser draft state.
- The frontend never invents management capability and never treats link visibility as authorization.
- Do not change VOC records, drafts, submission confirmation, management data, or DingTalk identity contracts.
- Do not introduce Mock production data or another landing route.

---

## File Structure

- Modify `webui/src/api.ts`: classify session bootstrap failures without parsing private backend detail.
- Modify `webui/src/api.test.ts`: lock 401, 403, 503, invalid-contract, and safe-return behavior.
- Create `webui/src/VocAccessState.tsx`: render bounded retry, login-transition, and management-denial states.
- Create `webui/src/VocAccessState.test.tsx`: test accessible actions and safe links.
- Modify `webui/src/App.tsx`: use an explicit bootstrap state machine and Agent-first navigation groups.
- Create `webui/src/App.test.tsx`: cover direct-use, management visibility, retry, and route isolation at the application level.
- Modify `webui/src/styles.css`: style the account and secondary management action without turning the page into a dashboard.
- Modify `webui/src/pages/VocManagementPage.test.tsx`: update the canonical return-link assertion.
- Modify `scripts/smoke_standalone_voc.py`: probe both canonical page shells without authentication material.
- Modify `tests/deploy/test_linux_mvp_contract.py`: gate the smoke coverage.

---

### Task 1: Give VOC Session bootstrap stable failure semantics

**Files:**
- Modify: `webui/src/api.ts`
- Test: `webui/src/api.test.ts`

**Interfaces:**
- Consumes: `GET /voc/session` and existing `safeCurrentVocLocation()`.
- Produces: `VocSessionLoadError.kind` in `redirecting | forbidden | unavailable | invalid`.

- [ ] **Step 1: Write failing tests for every bootstrap outcome**

Extend `api.test.ts`:

```typescript
it('classifies session failures without exposing backend detail', async () => {
  const cases = [
    [403, 'forbidden'],
    [503, 'unavailable'],
    [500, 'unavailable'],
  ] as const;
  for (const [status, kind] of cases) {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({
      detail: 'private backend diagnostic',
    }, status)));
    await expect(loadSession()).rejects.toMatchObject({ kind });
  }
});

it('classifies a successful but malformed contract as invalid', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ authenticated: true })));
  await expect(loadSession()).rejects.toMatchObject({ kind: 'invalid' });
});

it('marks login navigation as redirecting after preserving the safe route', async () => {
  history.replaceState({}, '', '/voc/manage/');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'required' }, 401)));
  const redirect = vi.fn();
  await expect(loadSession(redirect)).rejects.toMatchObject({ kind: 'redirecting' });
  expect(redirect).toHaveBeenCalledWith('/login?return_path=%2Fvoc%2Fmanage%2F');
});
```

- [ ] **Step 2: Run the focused tests and verify the untyped failures**

```bash
npm --prefix webui test -- --run api.test.ts
```

Expected: FAIL because errors do not have a `kind` field.

- [ ] **Step 3: Implement the typed error contract**

Add beside `VocApiError`:

```typescript
export type VocSessionFailureKind =
  | 'redirecting'
  | 'forbidden'
  | 'unavailable'
  | 'invalid';

export class VocSessionLoadError extends Error {
  constructor(public kind: VocSessionFailureKind) {
    super(`VOC session ${kind}`);
  }
}
```

Refine `loadSession` before normal body parsing:

```typescript
export async function loadSession(
  redirect: (path: string) => void = (path) => window.location.replace(path),
): Promise<VocSession> {
  let response: Response;
  try {
    response = await fetch(apiPath('/session'), { credentials: 'include' });
  } catch {
    throw new VocSessionLoadError('unavailable');
  }
  if (response.status === 401) {
    const returnPath = safeCurrentVocLocation();
    sessionStorage.setItem('voc:return', returnPath);
    redirect(`/login?return_path=${encodeURIComponent(returnPath)}`);
    throw new VocSessionLoadError('redirecting');
  }
  if (response.status === 403) throw new VocSessionLoadError('forbidden');
  if (!response.ok) throw new VocSessionLoadError('unavailable');
  try {
    return parseSession(await body(response));
  } catch {
    throw new VocSessionLoadError('invalid');
  }
}
```

Do not include response detail in the browser error.

- [ ] **Step 4: Run the complete API test file**

```bash
npm --prefix webui test -- --run api.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit the bootstrap contract**

```bash
git add webui/src/api.ts webui/src/api.test.ts
git commit -m "fix(voc): classify workspace identity failures"
```

---

### Task 2: Replace the generic full-page failure with recoverable states

**Files:**
- Create: `webui/src/VocAccessState.tsx`
- Create: `webui/src/VocAccessState.test.tsx`
- Modify: `webui/src/App.tsx`

**Interfaces:**
- Consumes: `VocSessionLoadError.kind` and a retry callback.
- Produces: `VocAccessState` plus an App bootstrap state of `loading | redirecting | forbidden | unavailable | invalid | ready`.

- [ ] **Step 1: Write the failing access-state component tests**

```tsx
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { VocAccessState } from './VocAccessState';

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
});

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const value = [...container.querySelectorAll('button')]
    .find((node) => node.textContent === label);
  if (!(value instanceof HTMLButtonElement)) throw new Error(`missing ${label}`);
  return value;
}

it('offers an in-place retry for transient identity failure', async () => {
  const retry = vi.fn();
  await act(async () => root.render(
    <VocAccessState kind="unavailable" onRetry={retry} />,
  ));
  expect(container.textContent).toContain('企业身份服务暂时不可用');
  await act(async () => button(container, '重新尝试').click());
  expect(retry).toHaveBeenCalledOnce();
});

it('returns an unauthorized manager to the VOC Agent', async () => {
  await act(async () => root.render(
    <VocAccessState kind="management-forbidden" onRetry={() => undefined} />,
  ));
  expect(container.querySelector<HTMLAnchorElement>("a[href='/voc/']")?.textContent)
    .toBe('返回 VOC Agent');
});
```

The component's public kind adds `management-forbidden` for the route-level denial, while the API error remains limited to the four bootstrap kinds.

- [ ] **Step 2: Run the test and verify the component is missing**

```bash
npm --prefix webui test -- --run VocAccessState.test.tsx
```

Expected: FAIL because `VocAccessState.tsx` does not exist.

- [ ] **Step 3: Implement bounded user-facing states**

```tsx
import type { VocSessionFailureKind } from './api';

export type VocAccessStateKind =
  | 'loading'
  | VocSessionFailureKind
  | 'management-forbidden';

const COPY = {
  loading: ['正在确认企业账号', '正在读取账号与授权范围。'],
  redirecting: ['正在前往企业登录', '登录完成后会返回当前 VOC 页面。'],
  forbidden: ['无法使用 VOC Agent', '当前企业账号没有 VOC 使用权限。'],
  unavailable: ['企业身份服务暂时不可用', '你的页面地址未改变，可以直接重试。'],
  invalid: ['暂时无法确认企业身份', '身份响应异常，请重新尝试。'],
  'management-forbidden': ['没有管理权限', '你仍然可以继续使用 VOC Agent。'],
} as const;
```

Render `重新尝试` only for `unavailable` and `invalid`; render `返回 VOC Agent`
only for `management-forbidden`; render no fake retry while the browser is
already redirecting.

Refactor `App.tsx` to one idempotent `beginSessionLoad()` callback. Every retry
sets state to `loading`, clears the prior failure, calls `loadSession`, and ignores
late results after unmount. Do not call `window.location.reload()`.

- [ ] **Step 4: Write and run an App retry regression**

Add `webui/src/App.test.tsx`:

```tsx
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  history.replaceState({}, '', '/voc/');
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const validSession = {
  authenticated: true,
  display_name: '苍渊',
  read_only: false,
  management_capabilities: { voc_admin: false },
  csrf_token: 'csrf',
};

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const value = [...container.querySelectorAll('button')]
    .find((node) => node.textContent === label);
  if (!(value instanceof HTMLButtonElement)) throw new Error(`missing ${label}`);
  return value;
}

it('recovers from a temporary Session failure without leaving /voc/', async () => {
  history.replaceState({}, '', '/voc/');
  let sessionAttempt = 0;
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const path = String(input);
    if (path === '/voc/session') {
      sessionAttempt += 1;
      return Promise.resolve(sessionAttempt === 1
        ? json({ detail: 'unavailable' }, 503)
        : json(validSession));
    }
    if (path === '/voc/api/v1/drafts/active') return Promise.resolve(json(null));
    if (path.startsWith('/voc/api/v1/vocs?')) {
      return Promise.resolve(json({ items: [] }));
    }
    return Promise.reject(new Error(`unexpected fetch ${path}`));
  });
  vi.stubGlobal('fetch', fetchMock);
  await act(async () => root.render(<App />));
  expect(container.textContent).toContain('企业身份服务暂时不可用');
  await act(async () => button(container, '重新尝试').click());
  expect(container.textContent).toContain('把客户声音，整理成可行动的记录');
  expect(window.location.pathname).toBe('/voc/');
});
```

Run:

```bash
npm --prefix webui test -- --run VocAccessState.test.tsx App.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit the recoverable state machine**

```bash
git add webui/src/VocAccessState.tsx webui/src/VocAccessState.test.tsx \
  webui/src/App.tsx webui/src/App.test.tsx
git commit -m "fix(voc): make workspace startup recoverable"
```

---

### Task 3: Make VOC navigation Agent-first and reciprocal

**Files:**
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/styles.css`
- Test: `webui/src/App.test.tsx`
- Test: `webui/src/pages/VocManagementPage.test.tsx`

**Interfaces:**
- Consumes: `session.management_capabilities.voc_admin` and canonical `vocRoutePath`.
- Produces: one Agent-home link, one user-records link, and at most one management link.

- [ ] **Step 1: Write failing navigation tests**

```tsx
function stubSession({ voc_admin }: { voc_admin: boolean }): void {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    if (String(input) === '/voc/session') {
      return Promise.resolve(json({
        ...validSession,
        management_capabilities: { voc_admin },
      }));
    }
    if (String(input).startsWith('/voc/api/v1/drafts/active')) {
      return Promise.resolve(json(null));
    }
    if (String(input).startsWith('/voc/api/v1/vocs?')) {
      return Promise.resolve(json({ items: [] }));
    }
    if (String(input) === '/voc/api/v1/admin/vocs?limit=50') {
      return Promise.resolve(json({ items: [], next_cursor: null }));
    }
    if (String(input) === '/voc/api/v1/admin/submitters') {
      return Promise.resolve(json({ items: [] }));
    }
    return Promise.reject(new Error(`unexpected fetch ${String(input)}`));
  }));
}

function stubManagementSession(): void {
  stubSession({ voc_admin: true });
}

it('keeps /voc/ as the usage homepage and hides management for a normal user', async () => {
  history.replaceState({}, '', '/voc/');
  stubSession({ voc_admin: false });
  await act(async () => root.render(<App />));
  expect(container.textContent).toContain('VOC 洞察助手');
  expect(container.querySelectorAll("a[href='/voc/manage/']")).toHaveLength(0);
  expect(container.querySelector<HTMLAnchorElement>("a[href='/voc/']")?.textContent)
    .toBe('VOC Agent');
});

it('shows exactly one management entry for an authorized user', async () => {
  history.replaceState({}, '', '/voc/');
  stubSession({ voc_admin: true });
  await act(async () => root.render(<App />));
  const links = container.querySelectorAll<HTMLAnchorElement>("a[href='/voc/manage/']");
  expect(links).toHaveLength(1);
  expect(links[0].textContent).toBe('管理工作台');
});

it('keeps the Agent return available inside management', async () => {
  history.replaceState({}, '', '/voc/manage/');
  stubManagementSession();
  await act(async () => root.render(<App />));
  expect(container.querySelector<HTMLAnchorElement>("a[href='/voc/']")?.textContent)
    .toBe('返回 VOC Agent');
});
```

- [ ] **Step 2: Run the navigation tests and verify copy/structure failures**

```bash
npm --prefix webui test -- --run App.test.tsx VocManagementPage.test.tsx
```

Expected: FAIL because the existing home link is `员工反馈` and management has no explicit return copy.

- [ ] **Step 3: Implement the grouped top bar**

Keep the account separate from navigation and select copy by route:

```tsx
import type { MouseEvent } from 'react';

const inManagement = route.name === 'management' || route.name === 'management-record';
const open = (event: MouseEvent<HTMLAnchorElement>, next: PathRoute) => {
  event.preventDefault();
  navigate(next);
};

<header className="voc-topbar">
  <span className="voc-account">欢迎，{session.display_name}</span>
  <nav aria-label="VOC 工作区">
    <a
      href={vocRoutePath({ name: 'feedback' })}
      onClick={(event) => open(event, { name: 'feedback' })}
    >
      {inManagement ? '返回 VOC Agent' : 'VOC Agent'}
    </a>
    {!inManagement && <a
      href={vocRoutePath({ name: 'records' })}
      onClick={(event) => open(event, { name: 'records' })}
    >我的 VOC</a>}
    {session.management_capabilities.voc_admin && !inManagement && (
      <a
        className="voc-management-entry"
        href={vocRoutePath({ name: 'management' })}
        onClick={(event) => open(event, { name: 'management' })}
      >
        管理工作台
      </a>
    )}
  </nav>
</header>
```

Use the same route helpers already present; do not introduce literal navigation
paths. Style the navigation as a compact flex group and keep it horizontally
scrollable on narrow screens rather than wrapping into dashboard rows.

- [ ] **Step 4: Run all WebUI tests and build**

```bash
npm --prefix webui test -- --run
npm --prefix webui run build
```

Expected: all tests PASS and the production build exits 0.

- [ ] **Step 5: Commit Agent-first navigation**

```bash
git add webui/src/App.tsx webui/src/App.test.tsx webui/src/styles.css \
  webui/src/pages/VocManagementPage.test.tsx
git commit -m "feat(voc): make Agent use the canonical workspace home"
```

---

### Task 4: Gate both canonical VOC shells and run authenticated acceptance

**Files:**
- Modify: `scripts/smoke_standalone_voc.py`
- Modify: `tests/deploy/test_linux_mvp_contract.py`
- Verify: `tests/api/test_voc_browser_api.py`
- Verify: `tests/acceptance/test_platform_workspace_contract.py`

**Interfaces:**
- Consumes: public `/voc/`, public `/voc/manage/`, protected `/voc/session`, and current Platform subject service.
- Produces: deploy evidence that shells, identity, and management capability agree.

- [ ] **Step 1: Add a failing deployment-contract assertion**

Require the smoke probe table to include both shells:

```python
assert '("public_workspace", f"{_PLATFORM_BASE}/voc/", False, 200)' in smoke
assert '("public_management_workspace", f"{_PLATFORM_BASE}/voc/manage/", False, 200)' in smoke
assert '("unauthenticated_session", f"{_PLATFORM_BASE}/voc/session", False, 401)' in smoke
```

- [ ] **Step 2: Run the deploy test and verify management shell coverage is absent**

```bash
python -m pytest tests/deploy/test_linux_mvp_contract.py -q
```

Expected: FAIL on `public_management_workspace`.

- [ ] **Step 3: Extend the content-free smoke probe**

Add exactly one row to `_probes()` in `scripts/smoke_standalone_voc.py`:

```python
('public_management_workspace', f'{_PLATFORM_BASE}/voc/manage/', False, 200),
```

Only assert shell marker/status. Do not add cookies, Session values, response
bodies, or management data to the smoke artifact.

- [ ] **Step 4: Run backend, WebUI, and cross-service contract tests**

```bash
python -m pytest \
  tests/api/test_voc_browser_api.py \
  tests/acceptance/test_platform_workspace_contract.py \
  tests/deploy/test_linux_mvp_contract.py -q
npm --prefix webui test -- --run
npm --prefix webui run build
```

Expected: PASS.

- [ ] **Step 5: Commit the deploy gate**

```bash
git add scripts/smoke_standalone_voc.py tests/deploy/test_linux_mvp_contract.py
git commit -m "test(voc): gate direct and management workspace shells"
```

- [ ] **Step 6: Run production acceptance without exposing credentials**

1. Record the current VOC release SHA and container identity.
2. Run the official VOC deploy process; do not modify Platform, FAE, Office, HR,
   Marketing, or shared Nginx ownership.
3. Run `scripts/smoke_standalone_voc.py` and the packaged workspace readiness
   verification.
4. Using the existing owner browser Session, open `/voc/`, submit no data, and
   verify the Agent page plus one management entry.
5. Open `/voc/manage/` and verify the management page plus `返回 VOC Agent`.
6. Using an ordinary member Session, verify `/voc/` works, the management entry
   is absent, and direct `/voc/manage/` produces the friendly denial state.
7. Temporarily exercise the test-only unavailable dependency in staging or the
   local acceptance harness; verify retry recovers without changing the route.
8. Verify direct refresh and mobile layout for `/voc/`, `/voc/records`, and
   `/voc/manage/`.

Acceptance evidence records only status, boolean assertions, container/release
identity, and timestamps. It must not capture VOC content, cookies, bearer
tokens, internal UUIDs, or raw Platform responses.
