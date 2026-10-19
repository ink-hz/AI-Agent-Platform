# SDK Requirement Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a real, enterprise-session SDK requirement workbench backed by ZenTao stories and tasks, with a local review record and safe failure behavior.

**Architecture:** The existing Platform application owns the browser UI and BFF. A bounded ZenTao adapter reads and writes only approved API v2 endpoints. A separate SDK schema records local members, request intents and review events; it never stores editable story text. Deployment assets provide a private ZenTao 22.6 trial.

**Tech Stack:** FastAPI, Pydantic v2, httpx, PostgreSQL/psycopg, React/TypeScript, Vite, Docker Compose.

## Global Constraints

- Preserve current Platform login, origin and CSRF enforcement.
- Do not integrate GitLab APIs, Webhooks, issues, merge requests or pipelines.
- ZenTao is authority for requirement text and task content; Platform is authority for SDK review decisions.
- All external writes carry a persistent request UUID; an ambiguous ZenTao result requires reconciliation and must not be silently retried.
- The feature is disabled until a private ZenTao origin, secret file, product ID and execution ID are explicitly configured.
- No browser direct access to ZenTao or its API token.
- Browser acceptance is performed by the user; API and component verification are developer owned.

---

### Task 1: Private ZenTao adapter

**Files:**
- Create: `backend/app/sdk_workbench/zentao.py`
- Create: `backend/tests/test_sdk_zentao.py`

**Interfaces:**
- Produces: `ZenTaoClient(base_url, account, password_file, transport=None)`, `async login()`, `async list_stories(product_id, page)`, `async get_story(story_id)`, `async create_story(product_id, title, spec, verify)`, `async create_task(execution_id, story_id, name, desc)`, `async close()`; raises `ZenTaoUnavailable`, `ZenTaoProtocolError`, `ZenTaoRejected`.

- [ ] **Step 1: Write failing tests** for exact `/api.php/v2/users/login`, stories and tasks paths, token header, pagination and bounded response. Use `httpx.MockTransport`; assert no request can go to a caller supplied origin.
- [ ] **Step 2: Run** `PYTHONPATH=backend ../../backend/.venv/bin/pytest -q backend/tests/test_sdk_zentao.py` and confirm the missing module failure.
- [ ] **Step 3: Implement adapter** with `trust_env=False`, `follow_redirects=False`, a fixed private origin, 10 second timeout and 1 MiB response cap. Validate `status=success` and positive IDs before returning. Read password file on construction; never log token or password.
- [ ] **Step 4: Run** the exact test command, expect all tests pass; commit adapter and tests.

### Task 2: Authenticated BFF and durable requirement operations

**Files:**
- Create: `backend/app/sdk_workbench/routes.py`, `backend/app/sdk_workbench/service.py`, `backend/app/sdk_workbench/repository.py`, `backend/control_migrations/113_sdk_workbench_requirements.sql`, `backend/tests/test_sdk_routes.py`
- Modify: `backend/app/control_plane/authorization.py`, `backend/app/control_plane/routes_auth.py`, `backend/app/main.py`, `backend/app/config.py`

**Interfaces:**
- Consumes: `ZenTaoClient` from Task 1.
- Produces: `GET /api/v1/sdk/me`, `GET/POST /api/v1/sdk/requirements`, `GET /api/v1/sdk/requirements/{story_id}`, `POST /api/v1/sdk/requirements/{story_id}/reviews`, `POST /api/v1/sdk/requirements/{story_id}/tasks`; `GET /sdk/` SPA route.

- [ ] **Step 1: Write failing route tests** with `ASGITransport` and fake actor context: no actor 401, no SDK grant 403, stale mutation 503, invalid body 422, authorized create delegates to ZenTao, repeated request UUID returns prior result, ambiguous create stays reconciliation required.
- [ ] **Step 2: Run** targeted tests and confirm expected failures.
- [ ] **Step 3: Add database migration** for `sdk.members`, `sdk.write_intents`, `sdk.review_events` with uniqueness on `(actor_id, request_id)` and append-only events. Add grants to app role following current control migration convention.
- [ ] **Step 4: Add service and routes** with strict request models, real actor UUID from `request.state.auth_context`, bounded page, fixed error codes, no-store responses and local review decision. Register explicit routes in central authorization list and app creation. Add `/sdk/` to protected SPA routes.
- [ ] **Step 5: Run** route tests and existing auth tests; commit.

### Task 3: React requirement workbench

**Files:**
- Create: `webui/src/sdk/SdkWorkbench.tsx`, `webui/src/sdk/sdkApi.ts`, `webui/src/sdk/SdkWorkbench.test.tsx`, `webui/src/sdk/sdkWorkbench.css`
- Modify: `webui/src/router.ts`, `webui/src/App.tsx`, `webui/src/platform/PlatformSidebar.tsx`, `webui/src/auth.ts`

**Interfaces:**
- Consumes: Task 2 API only; account CSRF token for writes.
- Produces: `/sdk/` requirements list, create form, story detail, local review and task create forms.

- [ ] **Step 1: Write failing component test** for list, create, detail and upstream failure messages. Verify submitted actor ID is absent and `X-CSRF-Token` is present.
- [ ] **Step 2: Run** `npm test -- --run src/sdk/SdkWorkbench.test.tsx` from `webui` and confirm failure.
- [ ] **Step 3: Implement** typed API helper, route integration and a compact responsive page using current Platform component conventions. Hide write controls based on `/sdk/me` capabilities, but rely on API enforcement.
- [ ] **Step 4: Run** component tests, `npm run build` and router tests; commit.

### Task 4: Trial deployment and real API verification

**Files:**
- Create: `deploy/sdk/compose.yaml`, `deploy/sdk/README.md`, `deploy/sdk/acceptance.sh`
- Modify: `docs/superpowers/specs/2026-10-19-sdk-development-v1-design.md` if trial reveals a contract correction.

**Interfaces:**
- A private ZenTao HTTP port reachable by Platform BFF only; MySQL persists in its own volume.

- [ ] **Step 1: Add image-pinned Compose** with no public ZenTao/MySQL port and secret-file initialization instructions.
- [ ] **Step 2: Validate** Compose config and start an isolated trial where Docker is available. Initialize product and execution, provision least privilege API account, then run create/read/task calls through the BFF with a real enterprise test identity. Record actual version, IDs, response shapes and failures.
- [ ] **Step 3: Run** targeted backend and frontend checks once, then commit deployment instructions and acceptance evidence. If private trial host or corporate identity is unavailable, report those as unverified; do not call the feature production ready.

## Next plan

After Task 4, write and execute a separate implementation plan for immutable candidate baseline, build and test evidence, release gate and traceability. That plan is required before claiming the full SDK v1 lifecycle is usable.
