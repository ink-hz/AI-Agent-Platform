# SDK ZenTao SSO Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An authorized DingTalk user can enter their own ZenTao account from the SDK workbench with one click.

**Architecture:** Platform SDK API issues a 45 second one-use ticket backed by Control DB. The ZenTao HTTPS virtual host forwards only its SSO consume path to the platform API, which redeems the ticket and obtains a ZenTao browser session over the private Docker network.

**Tech Stack:** FastAPI, PostgreSQL/psycopg, React/TypeScript, Nginx, ZenTao 22.6 legacy `apilogin`.

## Global Constraints

- Public ZenTao `apilogin` remains denied.
- Human account bindings are unique and cannot use `sdk-api` or `sdkadmin`.
- No ticket or entry key in a URL or browser script.
- No standalone `nginx -t -c` on the shared production host.
- ZenTao product privileges are not copied or widened by the platform.

---

### Task 1: Persist bindings and one-time tickets

**Files:** `backend/control_migrations/118_sdk_zentao_sso.sql`, `backend/app/sdk_workbench/sso_repository.py`, `backend/tests/test_sdk_sso_repository.py`.

**Interfaces:** `bind(actor, user_id, account)`, `issue(user_id, session_id, digest)`, `consume(digest) -> account | None`.

- [ ] Write tests for unique binding, denied reserved account, inactive membership, expired/replayed ticket, revoked session, and mapping change between issue and consume.
- [ ] Run the tests and confirm feature-missing failures.
- [ ] Add schema and repository with atomic claim; validate account syntax and restrict app DB privileges.
- [ ] Run the focused repository tests and commit.

### Task 2: SDK SSO service and API

**Files:** `backend/app/sdk_workbench/sso.py`, `backend/app/sdk_workbench/routes.py`, `backend/app/control_plane/middleware.py`, `backend/app/control_plane/authorization.py`, `backend/app/config.py`, `backend/app/main.py`, `backend/tests/test_sdk_sso_routes.py`.

**Interfaces:** `POST /api/v1/sdk/sso/start`, `POST /api/v1/sdk/sso/consume`, `PUT /api/v1/sdk/sso/bindings/{user_id}`.

- [ ] Write failing route tests covering authenticated start, CSRF, unauthorized mapping, consumed/expired ticket, wrong Host/Origin, bad ZenTao response, and secure Cookie.
- [ ] Run the tests and confirm expected failures.
- [ ] Implement config validation, exact public consume boundary, private ZenTao client, and routes. Keep the service disabled until configured.
- [ ] Run focused route/auth/config tests and commit.

### Task 3: Workbench entry and isolated deployment contract

**Files:** `webui/src/sdk/SdkWorkbench.tsx`, `webui/src/sdk/sdkApi.ts`, `deploy/sdk/zentao-domain.nginx.conf`, `deploy/cloud/compose.yaml`, `deploy/sdk/README.md`, relevant frontend/deployment tests.

- [ ] Write failing frontend and Nginx contract tests for POST handoff and direct `apilogin` denial.
- [ ] Run them to verify expected failures.
- [ ] Implement entry action, exact SSO proxy location, and secret-file configuration.
- [ ] Run focused frontend/deployment tests, build the frontend, and commit.

### Task 4: Real scenario verification and release

- [ ] Verify the legacy ZenTao session cookie on an isolated ZenTao instance using one test account and entry.
- [ ] Check no authorization escalation, replay, account switching, or ticket leakage.
- [ ] Merge verified commits to the mainline; use the production release guard and full Compose overlay.
- [ ] Back up ZenTao and Control DB, configure the secret and app entry, bind `cangyuan` after verifying the platform UUID, then release.
- [ ] Verify the actual HTTPS login path and adjacent administrative site assets and error logs; document exact deployed commit, tests, and any browser acceptance outstanding.
