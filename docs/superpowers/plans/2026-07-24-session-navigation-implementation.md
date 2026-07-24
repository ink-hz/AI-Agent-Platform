# Session Navigation and Product Naming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Sessions the single conversation-data entry point, preserve filter and scroll context across Session Replay, and apply contextual Orbbec Agent Platform browser titles.

**Architecture:** Keep all backend Session and PostgreSQL flywheel contracts unchanged. Add small pure frontend helpers for canonical Session query state, history-backed drill-down context, and route titles; wire them into the existing router and pages; retire the duplicate Flywheel page behind a history-replacing compatibility redirect.

**Tech Stack:** React 19, TypeScript 5.6, Vite 7, Vitest 2.1, jsdom, browser History API.

## Global Constraints

- Sessions is the only visible top-level conversation-data entry point.
- `/flywheel` must history-replace to `/sessions`; it must not create a Back-button loop.
- PostgreSQL flywheel ingestion, synchronization, APIs, schemas, retention, backups, and Trace capture remain unchanged.
- Sessions URL parameters are exactly `agent_id`, `source_kind`, and `q`; empty values are omitted.
- Supported `source_kind` values are exactly `metabot`, `fae`, and `admin`.
- A syntactically valid explicit Agent ID remains addressable even when absent from the visible selector.
- Return targets must be same-origin supported Platform paths.
- Scroll and return context are per browser-history entry and are not cross-device preferences.
- The static title fallback is exactly `Orbbec Agent Platform`.
- Source-language Agent names remain unchanged in contextual titles.
- Existing user-owned changes outside the listed files must not be staged or modified.

---

## File Structure

- Create `webui/src/sessionNavigation.ts`: canonical Session filter parsing and serialization.
- Create `webui/src/sessionNavigation.test.ts`: pure filter-contract tests.
- Create `webui/src/navigationContext.ts`: internal return-target validation, per-entry scroll capture, and restoration hook.
- Create `webui/src/navigationContext.test.tsx`: jsdom history and scroll tests.
- Create `webui/src/documentTitle.ts`: route-title resolver and React title hook.
- Create `webui/src/documentTitle.test.tsx`: pure and asynchronous title tests.
- Modify `webui/src/router.ts`: full-location comparison, replace navigation, state-aware navigation, and legacy redirect support.
- Modify `webui/src/router.test.ts`: router and legacy redirect coverage.
- Modify `webui/src/pages/SessionsPage.tsx`: URL-driven filter state and restored scroll.
- Create `webui/src/pages/SessionsPage.test.tsx`: URL/filter/request/return integration tests.
- Modify `webui/src/components/PlatformLink.tsx`: optional context-preserving drill-down navigation.
- Modify `webui/src/components/SessionListItem.tsx`: mark Session links as drill-down links.
- Modify `webui/src/pages/SessionDetailPage.tsx`: true-source Back behavior with direct-entry fallback.
- Create `webui/src/pages/SessionDetailPage.test.tsx`: origin and fallback behavior tests.
- Modify `webui/src/pages/AgentDetailPage.tsx`: restored scroll readiness and contextual Agent title.
- Modify `webui/src/pages/AgentDetailPage.test.tsx`: Agent-origin return and title coverage.
- Modify `webui/src/App.tsx`: route title baseline and `/flywheel` redirect component.
- Modify `webui/src/AppShell.tsx`: remove Flywheel navigation.
- Modify `webui/src/copy.ts` and `webui/src/copy.test.ts`: approved three-item navigation contract.
- Modify `webui/index.html`: static title fallback.
- Delete `webui/src/pages/FlywheelPage.tsx`, `webui/src/components/AgentDataSwitcher.tsx`, and `webui/src/agentDataBrowser.test.tsx`: remove unreachable duplicate UI.
- Modify `README.md`: document Sessions as the UI entry and `/flywheel` as a compatibility redirect.

---

### Task 1: Canonical Sessions URL State

**Files:**
- Create: `webui/src/sessionNavigation.ts`
- Create: `webui/src/sessionNavigation.test.ts`

**Interfaces:**
- Consumes: browser-compatible query strings.
- Produces: `SessionFilters`, `sessionFiltersFromSearch(search)`, and `sessionsPath(filters)` for SessionsPage and tests.

- [ ] **Step 1: Write the failing pure contract tests**

```ts
import { describe, expect, it } from "vitest";

import { sessionFiltersFromSearch, sessionsPath } from "./sessionNavigation";

describe("Session URL state", () => {
  it("round-trips Agent, source, and Unicode query text", () => {
    const path = sessionsPath({
      agent_id: "marketing-inbound-bot",
      source_kind: "metabot",
      q: "周报 机器人",
    });
    expect(path).toBe("/sessions?agent_id=marketing-inbound-bot&source_kind=metabot&q=%E5%91%A8%E6%8A%A5+%E6%9C%BA%E5%99%A8%E4%BA%BA");
    expect(sessionFiltersFromSearch(path.slice(path.indexOf("?")))).toEqual({
      agent_id: "marketing-inbound-bot",
      source_kind: "metabot",
      q: "周报 机器人",
    });
  });

  it("omits empty values and rejects unsupported sources", () => {
    expect(sessionFiltersFromSearch("?agent_id=test-bot&source_kind=other&q=%20%20")).toEqual({
      agent_id: "test-bot",
      source_kind: "",
      q: "",
    });
    expect(sessionsPath({ agent_id: "", source_kind: "", q: "" })).toBe("/sessions");
  });

  it("rejects malformed Agent IDs without rejecting Unicode search text", () => {
    expect(sessionFiltersFromSearch("?agent_id=bad%20id&q=%E6%9C%BA%E5%99%A8%E4%BA%BA")).toEqual({
      agent_id: "",
      source_kind: "",
      q: "机器人",
    });
  });
});
```

- [ ] **Step 2: Run the focused test and verify the missing-module failure**

Run: `cd webui && npm test -- src/sessionNavigation.test.ts`

Expected: FAIL because `./sessionNavigation` does not exist.

- [ ] **Step 3: Implement the minimal canonical parser and serializer**

```ts
export type SessionSource = "" | "metabot" | "fae" | "admin";

export type SessionFilters = {
  agent_id: string;
  source_kind: SessionSource;
  q: string;
};

const SOURCES = new Set<SessionSource>(["", "metabot", "fae", "admin"]);

function clean(value: string | null): string {
  return (value ?? "").trim();
}

function cleanAgentId(value: string | null): string {
  const candidate = clean(value);
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(candidate) ? candidate : "";
}

export function sessionFiltersFromSearch(search: string): SessionFilters {
  const params = new URLSearchParams(search);
  const source = clean(params.get("source_kind"));
  return {
    agent_id: cleanAgentId(params.get("agent_id")),
    source_kind: SOURCES.has(source as SessionSource) ? source as SessionSource : "",
    q: clean(params.get("q")),
  };
}

export function sessionsPath(filters: SessionFilters): string {
  const params = new URLSearchParams();
  if (filters.agent_id) params.set("agent_id", filters.agent_id);
  if (filters.source_kind) params.set("source_kind", filters.source_kind);
  if (filters.q) params.set("q", filters.q);
  const search = params.toString();
  return search ? `/sessions?${search}` : "/sessions";
}
```

- [ ] **Step 4: Run the focused tests**

Run: `cd webui && npm test -- src/sessionNavigation.test.ts`

Expected: 3 tests PASS.

- [ ] **Step 5: Commit the URL-state contract**

```bash
git add webui/src/sessionNavigation.ts webui/src/sessionNavigation.test.ts
git commit -m "feat: canonicalize Session filters"
```

---

### Task 2: Router and Legacy Flywheel Redirect

**Files:**
- Modify: `webui/src/router.ts`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/App.tsx`

**Interfaces:**
- Consumes: existing `Route`, `navigate(path)`, and browser History API.
- Produces: `NavigateOptions`, `currentLocationPath()`, `navigate(path, options)`, and `LegacyFlywheelRedirect` behavior used by later tasks.

- [ ] **Step 1: Extend router tests for search-aware and replace navigation**

Add jsdom coverage that sets `/sessions?agent_id=one`, calls `navigate` with a different query, and asserts the full URL changes. Spy on `history.replaceState` and assert the legacy redirect replaces `/flywheel` with `/sessions`.

```ts
/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest";

import { currentLocationPath, navigate } from "./router";

afterEach(() => {
  window.history.replaceState({}, "", "/");
  vi.restoreAllMocks();
});

describe("browser navigation", () => {
  it("treats search changes as navigation", () => {
    window.history.replaceState({}, "", "/sessions?agent_id=one");
    navigate("/sessions?agent_id=two", { replace: true });
    expect(currentLocationPath()).toBe("/sessions?agent_id=two");
  });

  it("preserves caller state", () => {
    navigate("/sessions/fae%3Aone", { state: { returnTo: "/sessions" } });
    expect(window.history.state).toEqual({ returnTo: "/sessions" });
  });
});
```

- [ ] **Step 2: Run router tests and verify failure**

Run: `cd webui && npm test -- src/router.test.ts`

Expected: FAIL because `currentLocationPath` and navigation options are not implemented.

- [ ] **Step 3: Add full-location and state-aware navigation**

Add these interfaces to `router.ts` and keep the existing `platform:navigate` event contract:

```ts
export type NavigateOptions = {
  replace?: boolean;
  state?: unknown;
};

export function currentLocationPath(): string {
  return `${window.location.pathname}${window.location.search}`;
}

export function navigate(path: string, options: NavigateOptions = {}): void {
  if (currentLocationPath() === path) return;
  const method = options.replace ? "replaceState" : "pushState";
  window.history[method](options.state ?? {}, "", path);
  window.dispatchEvent(new Event("platform:navigate"));
  if (!options.replace) window.requestAnimationFrame(() => window.scrollTo(0, 0));
}
```

- [ ] **Step 4: Replace the Flywheel page with an in-app compatibility redirect**

Remove the `FlywheelPage` import from `App.tsx`. Import `useEffect` from React, `LoadingState` from `./components/DataState`, and `navigate` from `./router`. Add a redirect component that runs once and preserves no obsolete history entry:

```tsx
function LegacyFlywheelRedirect() {
  useEffect(() => navigate("/sessions", { replace: true }), []);
  return <LoadingState label="Opening Sessions" />;
}
```

Keep `parseRoute("/flywheel")` as the legacy route and render `LegacyFlywheelRedirect` for that case. Change `routeSection({ name: "flywheel" })` to `null` because it no longer has a navigation item.

- [ ] **Step 5: Run router tests and the production typecheck/build**

Run: `cd webui && npm test -- src/router.test.ts && npm run build`

Expected: router tests PASS and Vite production build succeeds.

- [ ] **Step 6: Commit router behavior**

```bash
git add webui/src/router.ts webui/src/router.test.ts webui/src/App.tsx
git commit -m "feat: redirect legacy Flywheel route"
```

---

### Task 3: URL-Driven Sessions Filters

**Files:**
- Create: `webui/src/pages/SessionsPage.test.tsx`
- Modify: `webui/src/pages/SessionsPage.tsx`

**Interfaces:**
- Consumes: `SessionFilters`, `sessionFiltersFromSearch`, `sessionsPath`, `navigate`, `fetchSessions`.
- Produces: a Sessions page whose controls, URL, and API request share one canonical applied filter state.

- [ ] **Step 1: Write jsdom integration tests with mocked API responses**

Cover initial URL hydration, immediate Agent/source application, submitted query application, retry preservation, and `popstate` restoration. The first test must assert the exact request fields:

```tsx
it("hydrates filters from the URL and requests the same result", async () => {
  window.history.replaceState({}, "", "/sessions?agent_id=ai-fae-agent&source_kind=fae&q=Gemini");
  await act(async () => root.render(<SessionsPage />));
  await act(async () => undefined);

  expect(container.querySelector<HTMLSelectElement>('select[name="agent_id"]')?.value).toBe("ai-fae-agent");
  expect(container.querySelector<HTMLSelectElement>('select[name="source_kind"]')?.value).toBe("fae");
  expect(container.querySelector<HTMLInputElement>('input[name="q"]')?.value).toBe("Gemini");
  expect(globalThis.fetch).toHaveBeenCalledWith(
    expect.stringContaining("agent_id=ai-fae-agent"),
    expect.anything(),
  );
});
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `cd webui && npm test -- src/pages/SessionsPage.test.tsx`

Expected: FAIL because Sessions controls are not URL-driven and do not expose the named fields.

- [ ] **Step 3: Refactor SessionsPage to a canonical applied state**

Initialize both draft and applied state from `sessionFiltersFromSearch(window.location.search)`. Add `name="q"`, `name="agent_id"`, and `name="source_kind"` to the controls. Agent and source changes call one helper that updates state and then calls:

```ts
navigate(sessionsPath(nextFilters), { replace: true });
```

Search submission trims `q`, updates both draft and applied values, and performs the same replace navigation. Register `popstate` and `platform:navigate` listeners that re-read the URL and restore both states. Keep the existing AbortController behavior and request `fetchSessions` from the applied state only.

- [ ] **Step 4: Run Sessions tests**

Run: `cd webui && npm test -- src/pages/SessionsPage.test.tsx src/sessionNavigation.test.ts`

Expected: all focused tests PASS.

- [ ] **Step 5: Commit URL-driven Sessions**

```bash
git add webui/src/pages/SessionsPage.tsx webui/src/pages/SessionsPage.test.tsx
git commit -m "feat: preserve Session filters in URLs"
```

---

### Task 4: Context-Preserving Session Drill-Down

**Files:**
- Create: `webui/src/navigationContext.ts`
- Create: `webui/src/navigationContext.test.tsx`
- Modify: `webui/src/components/PlatformLink.tsx`
- Modify: `webui/src/components/SessionListItem.tsx`
- Modify: `webui/src/pages/SessionsPage.tsx`
- Modify: `webui/src/pages/SessionDetailPage.tsx`
- Create: `webui/src/pages/SessionDetailPage.test.tsx`
- Modify: `webui/src/pages/AgentDetailPage.tsx`
- Modify: `webui/src/pages/AgentDetailPage.test.tsx`

**Interfaces:**
- Consumes: `currentLocationPath`, `navigate`, `PlatformLink`, browser `history.state`, and page data-readiness flags.
- Produces: `SessionOriginState`, `captureSessionOrigin()`, `sessionOriginFromState()`, `sessionReturnTarget()`, `useHistoryScrollRestoration(ready)`, and `preserveSessionContext` on PlatformLink.

- [ ] **Step 1: Write failing history-state and validation tests**

```tsx
/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest";

import { captureSessionOrigin, sessionReturnTarget } from "./navigationContext";

describe("Session navigation context", () => {
  it("captures the exact internal URL and scroll position", () => {
    window.history.replaceState({}, "", "/sessions?agent_id=ai-fae-agent&q=Gemini");
    const state = captureSessionOrigin(640);
    expect(state).toEqual({
      sessionOrigin: {
        path: "/sessions?agent_id=ai-fae-agent&q=Gemini",
        scrollY: 640,
      },
    });
    expect(sessionReturnTarget(state)).toBe("/sessions?agent_id=ai-fae-agent&q=Gemini");
  });

  it("rejects external and unsupported return targets", () => {
    expect(sessionReturnTarget({ sessionOrigin: { path: "https://example.com", scrollY: 10 } })).toBeNull();
    expect(sessionReturnTarget({ sessionOrigin: { path: "//example.com", scrollY: 10 } })).toBeNull();
    expect(sessionReturnTarget({ sessionOrigin: { path: "/unknown", scrollY: 10 } })).toBeNull();
  });
});
```

- [ ] **Step 2: Run the focused tests and verify the missing-module failure**

Run: `cd webui && npm test -- src/navigationContext.test.tsx`

Expected: FAIL because `navigationContext` does not exist.

- [ ] **Step 3: Implement validated per-entry context and restoration**

Use the exact state shape from the tests and allow only `/`, `/agents`, `/agents/<id>`, `/sessions`, `/sessions/<key>`, and `/activity`, with optional query strings. `captureSessionOrigin(scrollY)` merges the current history state into the source entry with `replaceState` and returns a detail-entry state containing the same `sessionOrigin` object.

Implement the restoration hook with one restoration attempt per location and only after `ready` is true:

```ts
export function useHistoryScrollRestoration(ready: boolean): void {
  const restored = useRef<string | null>(null);
  const location = currentLocationPath();
  useLayoutEffect(() => {
    if (!ready || restored.current === location) return;
    restored.current = location;
    const scrollY = sessionOriginFromState(window.history.state)?.scrollY;
    if (typeof scrollY !== "number" || scrollY < 0) return;
    const frame = window.requestAnimationFrame(() => {
      const maximum = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
      window.scrollTo(0, Math.min(scrollY, maximum));
    });
    return () => window.cancelAnimationFrame(frame);
  }, [location, ready]);
}
```

- [ ] **Step 4: Mark Session rows as context-preserving links**

Extend `PlatformLink` with an optional `preserveSessionContext?: boolean`. For an unmodified primary click with this flag, call `captureSessionOrigin(window.scrollY)` before `navigate`, and pass the returned state to the new history entry. Preserve native behavior for modified clicks.

Set the flag on the root `PlatformLink` in `SessionListItem`:

```tsx
<PlatformLink
  className="session-row"
  href={`/sessions/${encodeURIComponent(session.session_key)}`}
  preserveSessionContext
>
```

- [ ] **Step 5: Implement true-source Back with direct-entry fallback**

In `SessionDetailPage`, resolve the validated target once from `window.history.state`. Render `← Back` when it exists and `← All Sessions` otherwise. The contextual Back click prevents default navigation and calls `window.history.back()`; its `href` remains the validated target for accessibility and modified-click behavior.

```tsx
const returnTarget = sessionReturnTarget(window.history.state);

<PlatformLink
  className="back-link"
  href={returnTarget ?? "/sessions"}
  onClick={(event) => {
    if (!returnTarget || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    window.history.back();
  }}
>
  {returnTarget ? "← Back" : "← All Sessions"}
</PlatformLink>
```

- [ ] **Step 6: Restore source-page scroll only after content is ready**

Call `useHistoryScrollRestoration(page !== null && !error)` in SessionsPage. Call `useHistoryScrollRestoration(Boolean(agent && sessions && fleetAgent !== undefined && !error))` before AgentDetailPage's conditional returns so hooks remain unconditional.

- [ ] **Step 7: Add integration tests for Sessions origin, Agent origin, direct entry, and two independent entries**

Mock `window.scrollTo`, `window.requestAnimationFrame`, History state, and API responses. Assert:

```ts
expect(window.scrollTo).toHaveBeenCalledWith(0, 640);
expect(container.textContent).toContain("← Back");
expect(container.querySelector<HTMLAnchorElement>(".back-link")?.href).toContain("/agents/test-bot");
```

For direct detail entry, replace history state with `{}` and assert `← All Sessions` and an `/sessions` href.

- [ ] **Step 8: Run all navigation-focused tests**

Run: `cd webui && npm test -- src/navigationContext.test.tsx src/pages/SessionsPage.test.tsx src/pages/SessionDetailPage.test.tsx src/pages/AgentDetailPage.test.tsx src/observability.test.tsx`

Expected: all focused tests PASS.

- [ ] **Step 9: Commit drill-down restoration**

```bash
git add webui/src/navigationContext.ts webui/src/navigationContext.test.tsx webui/src/components/PlatformLink.tsx webui/src/components/SessionListItem.tsx webui/src/pages/SessionsPage.tsx webui/src/pages/SessionDetailPage.tsx webui/src/pages/SessionDetailPage.test.tsx webui/src/pages/AgentDetailPage.tsx webui/src/pages/AgentDetailPage.test.tsx webui/src/observability.test.tsx
git commit -m "feat: restore Session browsing context"
```

---

### Task 5: Product Navigation and Contextual Browser Titles

**Files:**
- Create: `webui/src/documentTitle.ts`
- Create: `webui/src/documentTitle.test.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/pages/AgentDetailPage.tsx`
- Modify: `webui/src/pages/AgentDetailPage.test.tsx`
- Modify: `webui/src/copy.ts`
- Modify: `webui/src/copy.test.ts`
- Modify: `webui/index.html`
- Delete: `webui/src/pages/FlywheelPage.tsx`
- Delete: `webui/src/components/AgentDataSwitcher.tsx`
- Delete: `webui/src/agentDataBrowser.test.tsx`

**Interfaces:**
- Consumes: `Route` and the asynchronously loaded Agent display name.
- Produces: `PLATFORM_TITLE`, `routeDocumentTitle(route)`, and `useDocumentTitle(title)`.

- [ ] **Step 1: Write failing title and navigation tests**

```tsx
/** @vitest-environment jsdom */

import { describe, expect, it } from "vitest";

import { routeDocumentTitle } from "./documentTitle";

describe("document titles", () => {
  it("uses contextual Orbbec Agent Platform titles", () => {
    expect(routeDocumentTitle({ name: "overview" })).toBe("Orbbec Agent Platform");
    expect(routeDocumentTitle({ name: "agents" })).toBe("Agents · Orbbec Agent Platform");
    expect(routeDocumentTitle({ name: "agent", agentId: "one" })).toBe("Agent · Orbbec Agent Platform");
    expect(routeDocumentTitle({ name: "sessions" })).toBe("Sessions · Orbbec Agent Platform");
    expect(routeDocumentTitle({ name: "session", sessionKey: "one" })).toBe("Session Replay · Orbbec Agent Platform");
    expect(routeDocumentTitle({ name: "activity" })).toBe("Activity History · Orbbec Agent Platform");
    expect(routeDocumentTitle({ name: "not-found" })).toBe("Orbbec Agent Platform");
  });
});
```

Update `copy.test.ts` to require exactly `['Overview', 'Agents', 'Sessions']`. Add a source assertion that `webui/index.html` contains `<title>Orbbec Agent Platform</title>` and does not contain `MetaBot Cluster Monitor`.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd webui && npm test -- src/documentTitle.test.tsx src/copy.test.ts`

Expected: FAIL because the helper does not exist and Flywheel remains in navigation copy.

- [ ] **Step 3: Implement the title resolver and hook**

```ts
import { useEffect } from "react";

import type { Route } from "./router";

export const PLATFORM_TITLE = "Orbbec Agent Platform";

export function routeDocumentTitle(route: Route): string {
  switch (route.name) {
    case "agents": return `Agents · ${PLATFORM_TITLE}`;
    case "agent": return `Agent · ${PLATFORM_TITLE}`;
    case "sessions": return `Sessions · ${PLATFORM_TITLE}`;
    case "session": return `Session Replay · ${PLATFORM_TITLE}`;
    case "activity": return `Activity History · ${PLATFORM_TITLE}`;
    default: return PLATFORM_TITLE;
  }
}

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title;
  }, [title]);
}
```

Call `useDocumentTitle(routeDocumentTitle(route))` in App. In AgentDetailPage call `useDocumentTitle(agent ? `${agent.name} · ${PLATFORM_TITLE}` : `Agent · ${PLATFORM_TITLE}`)`; the Agent-specific effect runs when the canonical Agent response arrives.

- [ ] **Step 4: Remove the visible and unreachable Flywheel UI**

Remove the Flywheel entry from `AppShell.NAVIGATION` and `UI_COPY.navigation`. Delete the three obsolete Flywheel UI files listed above. Retain the router's legacy `/flywheel` case and redirect component from Task 2.

- [ ] **Step 5: Replace the static HTML title**

In `webui/index.html`, replace:

```html
<title>MetaBot Cluster Monitor</title>
```

with:

```html
<title>Orbbec Agent Platform</title>
```

- [ ] **Step 6: Run title, copy, router, and Agent tests**

Run: `cd webui && npm test -- src/documentTitle.test.tsx src/copy.test.ts src/router.test.ts src/pages/AgentDetailPage.test.tsx`

Expected: all focused tests PASS.

- [ ] **Step 7: Commit navigation and titles**

```bash
git add webui/src/documentTitle.ts webui/src/documentTitle.test.tsx webui/src/App.tsx webui/src/AppShell.tsx webui/src/pages/AgentDetailPage.tsx webui/src/pages/AgentDetailPage.test.tsx webui/src/copy.ts webui/src/copy.test.ts webui/index.html webui/src/pages/FlywheelPage.tsx webui/src/components/AgentDataSwitcher.tsx webui/src/agentDataBrowser.test.tsx
git commit -m "feat: unify Platform navigation and titles"
```

---

### Task 6: Documentation, Full Verification, and Local Deployment

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: completed frontend behavior from Tasks 1–5.
- Produces: current operator documentation and a verified local production deployment.

- [ ] **Step 1: Update operator documentation**

Change the README UI capability wording so Sessions is the single conversation inspection entry. Remove `/flywheel` from the list of normal dashboard routes and document:

```markdown
- Sessions: `http://127.0.0.1:8000/sessions`
- Legacy `/flywheel` bookmarks redirect to Sessions; PostgreSQL flywheel collection and APIs remain active.
```

Keep the existing flywheel API documentation because backend APIs are not removed.

- [ ] **Step 2: Run the complete frontend verification**

Run: `cd webui && npm test && npm run build`

Expected: all Vitest files PASS, TypeScript exits 0, and Vite writes `webui/dist` successfully.

- [ ] **Step 3: Confirm the diff contains no backend or user-owned files**

Run: `git status --short && git diff --check && git diff --stat`

Expected: only the README and frontend files named in this plan are part of this feature; existing `backend/app/health/normalizer.py`, `backend/tests/test_health_normalizer.py`, `.claude/`, `docs/2026-06-29-platform-flywheel-review-design.md`, `registry.local.yaml`, and untracked Logo files remain unstaged and unchanged by this work.

- [ ] **Step 4: Commit documentation**

```bash
git add README.md
git commit -m "docs: clarify Session data entry"
```

- [ ] **Step 5: Deploy the built frontend through the existing LaunchAgent**

Run: `launchctl kickstart -k gui/$(id -u)/com.orbbec.ai-agent-platform`

Expected: command exits 0 and the Platform restarts using `webui/dist`.

- [ ] **Step 6: Verify the production endpoints and redirect**

Run:

```bash
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/ | rg -o '<title>[^<]+</title>'
curl -fsS http://127.0.0.1:8000/flywheel | rg -o '<title>[^<]+</title>'
```

Expected: health returns successfully and both HTML responses contain `<title>Orbbec Agent Platform</title>`.

- [ ] **Step 7: Perform browser acceptance checks**

Open `http://127.0.0.1:8000/sessions`, apply Agent, Source, and Chinese search filters, scroll into the result list, open a Session, and use Back. Confirm the URL filters, controls, result set, and scroll position are restored. Repeat from Agent Detail Recent Sessions. Open a Session URL directly and confirm `All Sessions`. Open `/flywheel` and confirm the address becomes `/sessions`. Check contextual browser titles on Overview, Agents, Agent Detail, Sessions, Session Replay, and Activity History.

- [ ] **Step 8: Inspect LaunchAgent health and logs**

Run:

```bash
launchctl print gui/$(id -u)/com.orbbec.ai-agent-platform | rg 'state =|pid =|last exit code'
tail -n 80 /Users/neo/Library/Logs/OrbbecAI-Agent-Platform.stderr.log
```

Expected: service state is running, a PID is present, and no new startup error appears.

- [ ] **Step 9: Commit any verification-only README correction, otherwise leave the tree unchanged**

If browser verification reveals documentation wording that is factually wrong, edit only that wording, rerun `git diff --check`, and commit it with:

```bash
git add README.md
git commit -m "docs: correct Session navigation verification"
```

If no correction is needed, do not create an empty commit.
