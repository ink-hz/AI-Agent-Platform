# Account Bootstrap Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop authenticated route transitions from blocking on repeated identity checks and make transient account-read failures recoverable.

**Architecture:** Keep the account snapshot in the top-level React shell for the lifetime of the authenticated SPA. Put a bounded, one-retry policy inside the read-only `loadAccount` client, and expose explicit recovery actions from the existing access-state component.

**Tech Stack:** React 18, TypeScript, Fetch API, Vitest, jsdom.

## Global Constraints

- Account reads have a five-second deadline.
- Retry once only for network errors, client timeout, HTTP 502, or HTTP 504.
- Do not retry 401, 403, malformed responses, or directory 503 responses.
- Do not persist account data or CSRF values in browser storage.
- Server-side authorization remains authoritative for every protected request.
- Do not expose provider identifiers, tokens, raw backend errors, or stack traces.

---

### Task 1: Bounded account read

**Files:**
- Modify: `webui/src/auth.ts`
- Test: `webui/src/auth.test.ts`

**Interfaces:**
- Consumes: `checked(response: Response): Promise<Response>` and `parseAccount(value: unknown): Account`.
- Produces: unchanged public interface `loadAccount(prefix?: string): Promise<Account>` with internal timeout and retry.

- [ ] **Step 1: Write failing retry and timeout tests**

Add tests that make the first fetch return HTTP 502 or reject when its abort signal fires, then return a valid account on the second fetch. Assert that `loadAccount("")` resolves and fetch is called exactly twice. Add a 503 test asserting a single call.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `npm test -- src/auth.test.ts`

Expected: the 502 and timeout recovery tests fail because `loadAccount` currently performs one unbounded fetch.

- [ ] **Step 3: Implement the bounded read**

Add an internal `fetchAccount` function using `AbortController`, a 5,000 ms timer cleared in `finally`, and the existing parser. Make `loadAccount` perform at most two attempts separated by 200 ms. Retry only `PlatformApiError` statuses 502/504, `TypeError`, or an abort error; rethrow all other failures unchanged.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `npm test -- src/auth.test.ts`

Expected: all account, OAuth, and management client tests pass.

- [ ] **Step 5: Commit the bounded read**

```bash
git add webui/src/auth.ts webui/src/auth.test.ts
git commit -m "fix(identity): bound and retry account bootstrap"
```

### Task 2: Stable shell and recovery actions

**Files:**
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/styles.css`
- Test: `webui/src/cloudMode.test.tsx`

**Interfaces:**
- Consumes: `loadAccount(): Promise<Account>`, `platformPath(path: string): string`, and the existing SPA `navigate` event.
- Produces: account bootstrap keyed by authenticated-shell entry and an explicit retry counter; `AccessState` accepts optional recovery actions.

- [ ] **Step 1: Write failing navigation and recovery tests**

Render `App` with identity mode enabled and a successful account response. Navigate from `/account` to `/identity` and assert `/api/v1/account` was requested once. Add a failure case that asserts `重新尝试` and `重新登录` are visible, clicks `重新尝试`, then confirms a later valid response restores the account page.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `npm test -- src/cloudMode.test.tsx`

Expected: navigation performs a second account request and the failure state has no recovery controls.

- [ ] **Step 3: Implement stable bootstrap state**

Replace the effect dependency on `route.name` with a boolean representing login versus authenticated shell plus an explicit retry counter. Clear the visible failure at the start of an explicit retry. Keep account state across authenticated route changes. Extend `AccessState` with optional child actions, rendering a retry button and a normal link to `platformPath("/login")` on recoverable failures.

- [ ] **Step 4: Add focused action styling**

Add `.access-actions` styles that keep the two controls readable on desktop and mobile and reuse the existing brand colors, border radius, and font rules.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run: `npm test -- src/cloudMode.test.tsx`

Expected: navigation performs one account read, the failure controls render, and retry recovers without browser reload.

- [ ] **Step 6: Commit the shell fix**

```bash
git add webui/src/App.tsx webui/src/styles.css webui/src/cloudMode.test.tsx
git commit -m "fix(identity): keep account state across navigation"
```

### Task 3: Regression and production verification

**Files:**
- No additional source files.

**Interfaces:**
- Consumes: completed Tasks 1 and 2.
- Produces: tested, built, deployed production release.

- [ ] **Step 1: Run the complete frontend suite and build**

```bash
cd webui
npm test
npm run build
npm audit --omit=dev --audit-level=high
```

Expected: all tests pass, the TypeScript/Vite build succeeds, and the audit reports zero high-severity production vulnerabilities.

- [ ] **Step 2: Run repository safety checks**

```bash
cd ..
git diff --check
git status --short
```

Expected: no whitespace errors and only the intended committed changes.

- [ ] **Step 3: Push and deploy the verified release**

Push the feature branch and `master` to the same verified commit, then run `deploy/cloud/deploy.sh` with the protected production environment file.

- [ ] **Step 4: Execute production acceptance**

Run the production DingTalk acceptance script on the server and confirm the five Platform containers are healthy, the public root redirects to `/login`, `/api/health` returns 200, FAE identity/start time remain unchanged, and the release SHA equals the pushed commit.

- [ ] **Step 5: Inspect post-deploy account access**

Confirm `/api/v1/account` returns 200 for an existing authenticated browser, route navigation does not add another account request, and no new 502 appears after the health-gated release completes.
