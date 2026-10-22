# SDK Native Requirements Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/sdk/` a usable requirements workspace backed by each employee's ZenTao identity and the existing Q4 stories.

**Architecture:** A platform-owned React workspace calls a narrow FastAPI facade. The facade checks current DingTalk eligibility, obtains a server-only personal ZenTao session using the existing SSO entry, and calls fixed private ZenTao API paths. ZenTao owns requirements, tasks, and review history; the platform stores only source supplements and write intents.

**Tech Stack:** FastAPI, Python 3, httpx, PostgreSQL, React 19, TypeScript, Vitest, pytest, ZenTao 22.6.

## Global Constraints

- No new DNS record or production Nginx edit; never run `nginx -t -c` on production.
- Never use the `sdk-api` token for employee writes; never send a ZenTao session ID to the browser or logs.
- Recheck current SDK eligibility on each request; ZenTao's product permissions remain authoritative.
- Do not put import IDs, hashes, or raw CSV rows in the user-facing requirement description.
- The 67 Q4 stories stay in place; unknown proposer names remain unknown.
- Run API and backend tests before frontend interaction; the business user performs final browser acceptance.

---

### Task 1: Personal ZenTao API transport

**Files:** Create `backend/app/sdk_workbench/native_client.py`; test `backend/tests/test_sdk_native_client.py`; modify `backend/app/sdk_workbench/sso.py` only to expose the existing server-side login method through a bounded interface.

**Interfaces:** `NativeZenTaoClient.request(actor: AuthContext, method: str, path: str, payload: dict | None = None, params: dict | None = None) -> dict`. It consumes `SdkSsoService.repository.identity()` and `_zen_login_sync()`; callers can pass only a fixed route enum/validated IDs. It never accepts an arbitrary URL.

- [ ] Write a failing `httpx.MockTransport` test: two actors bound to different accounts send `Token: ss_<different sid>`; an ineligible actor triggers no ZenTao call; an invalid session is discarded and retried once.
- [ ] Run `PYTHONPATH=backend backend/.venv/bin/python -m pytest -q backend/tests/test_sdk_native_client.py` from repo root and confirm the new test fails for missing behavior.
- [ ] Implement a bounded session cache keyed by `(internal_user_id, account)`, with 15-minute expiry and 1,000-entry maximum; verify eligibility before cache lookup. Map ZenTao 401/403, timeout, oversized or malformed JSON to controlled errors. Use `run_in_threadpool` for the existing synchronous login slot.
- [ ] Rerun the targeted test and confirm all cases pass; commit the transport and test.

### Task 2: Native read model and source supplement

**Files:** Create `backend/app/sdk_workbench/native_service.py`, `native_repository.py`, `native_routes.py`; modify `backend/app/main.py`; test `backend/tests/test_sdk_native_routes.py`.

**Interfaces:** `GET /api/v1/sdk/native/requirements?page=&view=&query=&status=` returns `{items,page,total}`; `GET /api/v1/sdk/native/requirements/{id}` returns a bounded story projection with source supplement and actions; `GET /api/v1/sdk/native/my-tasks` returns the employee's task page. `view=q4` selects stories whose ZenTao `sourceNote` starts with `2026年Q4上线版本`.

- [ ] Write failing route tests for anonymous/withdrawn SDK access, Q4 filtering, product scope, pagination, missing story, action history, source proposer versus ZenTao opener, and task listing.
- [ ] Confirm the tests fail on absent routes.
- [ ] Implement projections from ZenTao v2 response fields, read-only join against `sdk.plan_story_sources`, fixed product ID, 50-row pages, no-store responses, and the FastAPI router installation.
- [ ] Rerun route tests plus existing SDK SSO/legacy tests; commit.

### Task 3: Native write intents and requirement operations

**Files:** Add `backend/control_migrations/122_sdk_native_requirements.sql`; extend `native_repository.py`, `native_service.py`, `native_routes.py`; test `backend/tests/test_sdk_native_writes.py` and migration tests.

**Interfaces:** `POST /api/v1/sdk/native/requirements` creates a draft or reviewing story with `request_id`, title, scenario, device/platform, acceptance, priority, optional business proposer and reviewer. `PUT /api/v1/sdk/native/requirements/{id}` changes editable fields only with expected ZenTao version. `POST /api/v1/sdk/native/requirements/{id}/assign` and `/review` use expected version and personal session. Structured extra fields and write intents live in the platform DB; lifecycle/status are read from ZenTao.

- [ ] Write failing tests: creation sends the employee's session, not `sdk-api`; no reviewer leaves draft; selected reviewer enters review; timeout yields a reconcilable intent and repeat request never duplicates; edit/assign/review reject stale version and out-of-product IDs; review action is visible in ZenTao readback.
- [ ] Confirm failures are due to missing behavior.
- [ ] Add migration for native intent kinds and supplemental proposer/device fields, with unique `(actor_id,request_id)` and `story_id` constraints. Implement exact ZenTao v1 write payloads and readback verification. For unknown results, return a stable reconciliation status without resubmitting.
- [ ] Run targeted tests and migration checks, then commit.

### Task 4: Focused SDK UI

**Files:** Create `webui/src/sdk/NativeSdkWorkspace.tsx`, `nativeSdkApi.ts`, `nativeSdkWorkspace.css`; modify `webui/src/sdk/SdkWorkbench.tsx`, `webui/src/AppShell.tsx`, `webui/src/router.ts`, `webui/src/auth.ts`; test `webui/src/sdk/NativeSdkWorkspace.test.tsx` and relevant router/auth tests.

**Interfaces:** `/sdk/` opens the Q4 requirements view; `/sdk/requirements/{id}` deep-links to details; the workspace has requirements and my tasks views, a searchable list, source detail, creation/edit/assignment/review forms, and explicit busy/error/reconciliation states.

- [ ] Write failing component tests for Q4 list/selection, business proposer versus opener, form validation, write button disable while pending, mutation readback, and task view. Write router tests for direct links.
- [ ] Confirm the tests fail; implement the dedicated layout and API client. Sanitize ZenTao HTML using existing DOMPurify dependency. Hide the general platform sidebar on SDK routes and keep a clear return path to the main platform.
- [ ] Run targeted Vitest tests and `npm run build` from `webui`; commit.

### Task 5: Integration, release, and scenario evidence

**Files:** Add a focused test/evidence note under `docs/reviews/`; update `deploy/sdk` only if API environment wiring demands it. No Nginx config changes.

- [ ] Run SDK backend regressions, migration tests, frontend SDK tests, and production build; record exact counts and commit.
- [ ] Use an isolated ZenTao 22.6 instance to test employee A creating, employee B reviewing, version conflict, task list, and permission withdrawal; verify ZenTao operation actors from API/history.
- [ ] Merge reviewed changes to `master`, build from the exact merged SHA, back up current API release, deploy only platform API/web assets through the guarded release workflow.
- [ ] Check `/api/health`, `/sdk/`, native requirement API under a real platform identity, Q4 story count and source fields, SDK/administrative JS bytes and hash, container state, and recent permission errors. Record release SHA, image ID, rollback path, passed/failed/unrun cases, and browser acceptance remaining with the business user.
