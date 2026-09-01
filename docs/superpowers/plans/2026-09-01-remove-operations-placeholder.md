# Remove Operations Placeholder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the misleading “数据飞轮” placeholder entry while preserving every live Operations backend and consumer.

**Architecture:** Treat `/admin/operations` and `/flywheel` as historical client-side aliases of `/admin`. Remove the dedicated route variant, placeholder page dispatch, navigation item, and document title; leave all Operations APIs, services, repositories, synchronization, and existing consumers unchanged.

**Tech Stack:** React 19, TypeScript, Vitest, Vite, Git worktrees

## Global Constraints

- Modify only frontend routing, navigation, page dispatch, title mapping, and their tests.
- Preserve `/api/operations/*`, Operations backend modules, Daily Brief, Agent activity, databases, synchronization, cloud replica, deployment configuration, and FAE workbench behavior.
- Preserve query strings through the existing `LegacyRedirect` component.
- Work only in the `chore/remove-operations-placeholder` isolated worktree.
- Do not modify or clean other worktrees, branches, or untracked files.
- Do not push or deploy without explicit authorization.

## File Map

- `webui/src/router.ts`: canonical route model and historical redirect mapping.
- `webui/src/AppShell.tsx`: management navigation entries.
- `webui/src/App.tsx`: route-to-page dispatch.
- `webui/src/documentTitle.ts`: route-to-browser-title mapping.
- `webui/src/operations.ts`: canonical target paths for Operations events.
- `webui/src/router.brain.test.ts`: management namespace and historical redirect contract.
- `webui/src/router.test.ts`: navigation-section contract.
- `webui/src/AppShell.brain.test.tsx`: visible management navigation contract.
- `webui/src/documentTitle.test.tsx`: title fallback contract for historical redirects.
- `webui/src/operations.test.ts`: Operations event target-path contract.

---

### Task 1: Remove the placeholder product surface

**Files:**
- Modify: `webui/src/router.brain.test.ts`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/AppShell.brain.test.tsx`
- Modify: `webui/src/documentTitle.test.tsx`
- Modify: `webui/src/operations.test.ts`
- Modify: `webui/src/router.ts`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/documentTitle.ts`
- Modify: `webui/src/operations.ts`

**Interfaces:**
- Consumes: `Route`, `parseRoute(pathname: string): Route`, `LegacyRedirect`, and `ADMIN_NAVIGATION` from the existing frontend.
- Produces: `/admin/operations` and `/flywheel` both parse as `{ name: "legacy-redirect", to: "/admin" }`; no `admin-operations` route or navigation item remains.

- [ ] **Step 1: Write the failing route and navigation tests**

In `webui/src/router.brain.test.ts`, remove the old product-route assertion and add `/admin/operations` to the historical redirect table:

```ts
expect(parseRoute("/admin/activity")).toEqual({ name: "admin-activity" });
expect(parseRoute("/admin/identity")).toEqual({ name: "admin-identity" });

it.each([
  ["/review", "/admin/review"],
  ["/activity", "/admin/activity"],
  ["/admin/operations", "/admin"],
  ["/flywheel", "/admin"],
  ["/identity", "/admin/identity"],
  ["/governance", "/admin/governance"],
  ["/sessions", "/admin/sessions"],
  ["/sessions/fae%3Aone", "/admin/sessions/fae%3Aone"],
  ["/agents/hr-bot/runtime", "/admin/agents/hr-bot/runtime"],
])("has an explicit permanent client redirect from %s", (legacy, target) => {
  expect(parseRoute(legacy)).toEqual({ name: "legacy-redirect", to: target });
});
```

In `webui/src/AppShell.brain.test.tsx`, extend the owner-navigation test:

```ts
expect(container.querySelector<HTMLAnchorElement>('a[href="/admin/voc"]')?.textContent).toBe("VOC 管理");
expect(container.querySelector('.admin-nav a[href="/admin/operations"]')).toBeNull();
expect(container.querySelector(".admin-nav")?.textContent).not.toContain("数据飞轮");
```

In `webui/src/router.test.ts`, make the section test use the canonical redirect target:

```ts
expect(routeSection({ name: "legacy-redirect", to: "/admin" })).toBeNull();
```

In `webui/src/documentTitle.test.tsx`, use the canonical redirect target:

```ts
expect(routeDocumentTitle({ name: "legacy-redirect", to: "/admin" })).toBe(PLATFORM_TITLE);
```

In `webui/src/operations.test.ts`, require old Operations event targets to produce canonical management links:

```ts
expect(eventTargetPath({ ...event, target_path: "/flywheel" })).toBe("/admin");
expect(eventTargetPath({ ...event, target_path: "/admin/operations?period=7d" })).toBe("/admin?period=7d");
```

- [ ] **Step 2: Run the focused tests to prove the old behavior fails**

Run:

```bash
cd webui
npm test -- src/operations.test.ts src/router.test.ts src/router.brain.test.ts src/AppShell.brain.test.tsx src/documentTitle.test.tsx
```

Expected: FAIL because `/admin/operations` still parses as `admin-operations`, `/flywheel` still targets `/admin/operations`, Operations event links still generate the stale target, and the management navigation still renders “数据飞轮”.

- [ ] **Step 3: Implement the minimal route and navigation removal**

In `webui/src/router.ts`:

1. Delete this `Route` union member:

```ts
| { name: "admin-operations" }
```

2. Replace the current `/admin/operations` product route with a compatibility redirect:

```ts
if (clean === "/admin/operations") return { name: "legacy-redirect", to: "/admin" };
```

3. Change the legacy Flywheel redirect:

```ts
if (clean === "/flywheel") return { name: "legacy-redirect", to: "/admin" };
```

4. Delete the `routePath` switch arm:

```ts
case "admin-operations": return "/admin/operations";
```

In `webui/src/AppShell.tsx`, delete only this navigation entry:

```ts
{ label: "数据飞轮", path: "/admin/operations", section: "admin" },
```

In `webui/src/App.tsx`, delete only this page-dispatch arm:

```tsx
case "admin-operations": return <PendingPage title="Operations 与数据飞轮" description="运行摘要、证据和改进闭环仍由管理中心统一维护。" />;
```

In `webui/src/documentTitle.ts`, delete only this title arm:

```ts
case "admin-operations": return `Operations · ${PLATFORM_TITLE}`;
```

In `webui/src/operations.ts`, canonicalize both historical event target forms and narrow the pass-through rule to Activity:

```ts
if (path === "/flywheel") return "/admin";
if (/^\/admin\/operations(?:\?[^#]*)?$/.test(path)) {
  return path.replace(/^\/admin\/operations/, "/admin");
}
if (/^\/admin\/activity(?:\?[^#]*)?$/.test(path)) return path;
```

- [ ] **Step 4: Run focused tests and TypeScript production build**

Run:

```bash
cd webui
npm test -- src/operations.test.ts src/router.test.ts src/router.brain.test.ts src/AppShell.brain.test.tsx src/documentTitle.test.tsx
npm run build
```

Expected: all focused tests PASS; TypeScript and Vite build exit 0 with no reference to `admin-operations`.

- [ ] **Step 5: Confirm Operations backend and consumers are untouched**

Run from the repository root:

```bash
git diff --name-only
git diff -- backend/app backend/tests deploy
rg -n "fetchOperationsBrief|/api/operations/|operations_service" webui/src backend/app
```

Expected: the backend/deploy diff is empty; the search still finds the existing Daily Brief, Agent activity, API routes, and Operations service wiring.

- [ ] **Step 6: Commit the product change**

```bash
git add webui/src/router.ts webui/src/router.brain.test.ts webui/src/router.test.ts webui/src/AppShell.tsx webui/src/AppShell.brain.test.tsx webui/src/App.tsx webui/src/documentTitle.ts webui/src/documentTitle.test.tsx webui/src/operations.ts webui/src/operations.test.ts
git commit -m "fix(web): remove operations placeholder entry"
```

---

### Task 2: Verify against the latest shared mainline

**Files:**
- Verify only: all files changed by Task 1

**Interfaces:**
- Consumes: committed Task 1 branch and the latest local `master`.
- Produces: a clean, merge-ready branch whose only product change is removal of the Operations placeholder surface.

- [ ] **Step 1: Inspect mainline movement and changed-file overlap**

Run from the isolated worktree:

```bash
git log --oneline HEAD..master
git diff --name-only HEAD...master
git status --short
```

Expected: worktree is clean. If `master` changed any Task 1 file, stop and reconcile those changes explicitly before continuing.

- [ ] **Step 2: Rebase onto the latest local mainline when necessary**

If `git log --oneline HEAD..master` is non-empty, run:

```bash
git rebase master
```

Expected: rebase succeeds without dropping other sessions’ commits. If a conflict occurs, preserve both the latest mainline behavior and the approved placeholder-removal contract, then rerun all verification.

- [ ] **Step 3: Run the complete frontend test suite**

```bash
cd webui
npm test -- --run
```

Expected: 70 test files and at least 612 tests PASS, with zero failures.

- [ ] **Step 4: Run the production build and repository hygiene checks**

```bash
cd webui
npm run build
cd ..
git diff --check master...HEAD
git status --short
```

Expected: production build exits 0; diff check is clean; worktree has no uncommitted tracked changes.

- [ ] **Step 5: Review the exact merge surface**

```bash
git diff --stat master...HEAD
git diff --name-status master...HEAD
git log --oneline master..HEAD
```

Expected: one design commit, one plan commit, and one frontend implementation commit; no backend, database, deployment, unrelated worktree, or untracked file appears in the diff.
