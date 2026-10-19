# SDK Live Acceptance and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate SDK V1 against a real ZenTao instance and real SDK evidence, then publish it at `https://agent.orbbec.com.cn/sdk/` through the existing platform release path.

**Architecture:** Keep ZenTao in a separate private Compose project and the SDK UI/API in the existing Platform origin. Run local HTTP/PostgreSQL regressions, a real ZenTao API trial, and an isolated end-to-end SDK workflow before a versioned cloud release. Activate the SDK flag only after migrations, secrets, roles, backups and rollback evidence are verified.

**Tech Stack:** FastAPI, psycopg/PostgreSQL, React/Vite, ZenTao OSS 22.6, MariaDB 10.6, Docker Compose, Bash.

## Global Constraints

- No GitLab API, webhook or development-flow synchronization in V1.
- No fabricated build, unit-test or hardware-test result; missing hardware is `blocked`.
- Retain SDK write `request_id`, immutable events, actor separation and `evidence_version` checks.
- Keep ZenTao private; employee URL is the Platform origin `/sdk/`.
- Preserve unrelated untracked files, current release history and other workbenches.
- The user performs final browser/page acceptance under `AGENTS.md`.

---

### Task 1: Local prerequisites and unchanged regression baseline

**Files:**
- Read: `deploy/sdk/compose.yaml`, `deploy/sdk/README.md`, `backend/tests/test_sdk_*.py`, `webui/src/sdk/*.test.tsx`

**Interfaces:**
- Consumes: committed SDK V1 `25c63551`.
- Produces: command output with pass/fail counts and local Docker availability.

- [ ] **Step 1:** Confirm the feature worktree is clean and source SHA manifest is available: `git status --short`, `git -C ../AI-SDK-Platform-sources/openorbbecsdkgroup submodule status` (use the actual absolute source path if invoked from the worktree).
- [ ] **Step 2:** Use an isolated local container engine; on this Mac run `brew install colima docker docker-compose`, `colima start --cpu 4 --memory 8`, `docker compose version`. Record the engine/image architecture and avoid the production Docker context.
- [ ] **Step 3:** Run `backend/.venv/bin/python -m pytest -q tests/test_control_plane_migration.py tests/test_r1_authorization.py tests/test_sdk_zentao.py tests/test_sdk_repository.py tests/test_sdk_routes.py tests/test_sdk_evidence_repository.py tests/test_sdk_evidence_routes.py` from `backend/`; use the main checkout's venv if the worktree has none. Expect 748 passing tests before any new test code.
- [ ] **Step 4:** Run `npm ci`, `npm test -- --run src/sdk src/pages/PermissionAccessPage.test.tsx src/router.test.ts`, and `npm run build` from `webui/`. Expect 88 passing relevant tests and a successful build before changes.

### Task 2: Real ZenTao 22.6 adapter trial

**Files:**
- Modify: `deploy/sdk/README.md`
- Create: `backend/tests/test_sdk_zentao_live.py`
- Read: `backend/app/sdk_workbench/zentao.py`, `backend/tests/test_sdk_zentao.py`

**Interfaces:**
- Consumes: `ZenTaoClient(base_url, account, password_file)` and a dedicated test product/execution in an isolated instance.
- Produces: live login, story creation/read/list, task creation/read observations with actual IDs; never a production business story.

- [ ] **Step 1:** Add an opt-in `@pytest.mark.live_zentao` test that reads `SDK_LIVE_ZENTAO_URL`, `SDK_LIVE_ZENTAO_ACCOUNT`, `SDK_LIVE_ZENTAO_PASSWORD_FILE`, `SDK_LIVE_PRODUCT_ID`, and `SDK_LIVE_EXECUTION_ID`. Skip when any variable is absent. A unique title beginning `SDK-V1-ACCEPT-` makes test records identifiable.
- [ ] **Step 2:** In that test, call `create_story(product_id,title,spec,verify)`, then `get_story(id)` and `list_stories(product_id,1)`; assert product, title, `spec`, `verify` and positive version. Call `create_task(execution_id,story_id,name,desc)` then `get_task(id)`; assert execution, story, name and desc. Close the client in `finally`.
- [ ] **Step 3:** Run the existing MockTransport test first; then run only the opt-in live test against the isolated ZenTao. Capture IDs, version, image digest and failures without printing the token or password. If the actual API normalizes content, compare exact observed fields and update the adapter contract or reconciliation rules before continuing.
- [ ] **Step 4:** Document isolated Compose startup, initialization, credentials file, product/execution creation, `pytest -m live_zentao`, and cleanup/retention in `deploy/sdk/README.md`. Commit the adapter trial and documentation.

### Task 3: Platform release wiring and isolated full-path test

**Files:**
- Modify: `deploy/cloud/compose.yaml`
- Modify: `deploy/sdk/README.md`
- Test: `backend/tests/test_sdk_routes.py`, `backend/tests/test_sdk_evidence_routes.py`

**Interfaces:**
- Consumes: `PLATFORM_SDK_WORKBENCH_ENABLED`, `PLATFORM_SDK_ZENTAO_*` from `backend/app/config.py`; shared `platform-api-secrets` volume.
- Produces: disabled-by-default cloud API configuration that can read `/run/secrets/sdk-zentao-password` when enabled.

- [ ] **Step 1:** Add a Compose configuration regression test that renders cloud Compose with SDK disabled and asserts the SDK flag defaults to `0`; render with enabled test values and assert base URL, account, password path, product ID and execution ID propagate only to `platform-api`.
- [ ] **Step 2:** Add the six SDK environment keys under `platform-api` in `deploy/cloud/compose.yaml`, using defaults that keep startup disabled: `PLATFORM_SDK_WORKBENCH_ENABLED: "${PLATFORM_SDK_WORKBENCH_ENABLED:-0}"`, `PLATFORM_SDK_ZENTAO_BASE_URL: "${PLATFORM_SDK_ZENTAO_BASE_URL:-}"`, `PLATFORM_SDK_ZENTAO_ACCOUNT: "${PLATFORM_SDK_ZENTAO_ACCOUNT:-}"`, `PLATFORM_SDK_ZENTAO_PASSWORD_FILE: /run/secrets/sdk-zentao-password`, and default `0` for product/execution IDs. The existing `platform-api-secrets` volume supplies the file only when enabled.
- [ ] **Step 3:** Re-run SDK HTTP/PostgreSQL tests, Compose config rendering, front-end relevant tests and build. In the isolated instance, use actual ZenTao IDs with a local platform API/temporary control database to create requirement → review → task → candidate; record `blocked` for hardware not present and assert gate stays closed. Do not turn the test data into a production release.
- [ ] **Step 4:** Commit the cloud configuration and updated runbook after review.

### Task 4: Cloud preflight, publish and rollback evidence

**Files:**
- Modify: `deploy/sdk/README.md`
- Create: `docs/reviews/2026-10-19-sdk-v1-release-evidence.md`
- Read: `deploy/cloud/deploy.sh`, `deploy/cloud/remote-stage.sh`, `deploy/cloud/acceptance.sh`, `docs/runbooks/cloud-platform.md`

**Interfaces:**
- Consumes: reviewed main commit, real ZenTao trial result, source/release digests, cloud deploy configuration and SDK migration checksums.
- Produces: public SDK URL plus a release record that distinguishes code publish, feature activation, real business acceptance and user page acceptance.

- [ ] **Step 1:** Read-only preflight: compare local `master`, `origin/master`, live `current` SHA, Docker/network capacity, ZenTao isolation, secret file mode/owner, release locks, control migration 114/115 state and backups. Record exact values without secret contents.
- [ ] **Step 2:** Verify the exact release commit on a clean worktree; push that commit to the remote mainline only after relevant tests and review. The existing `deploy/cloud/deploy.sh` checks clean worktree and `origin/master` equality; use its normal release path with the SDK flag still `0` and retain the prior release SHA.
- [ ] **Step 3:** Confirm existing Platform routes and health remain good, verify 114/115 in both control databases, and verify the new image contains `/sdk/` while the feature remains disabled. Start private ZenTao on its own Compose project and back up both named volumes before enabling.
- [ ] **Step 4:** Install minimal-rights ZenTao service account and write its password to a platform API readable mode-0600 file without logging it. Set the SDK environment values in the private deploy configuration, release the reviewed enabled configuration and verify `/api/v1/sdk/me`, authorization rejection and one real authenticated requirement/task path. If any gate fails, disable the SDK flag and retain all evidence.
- [ ] **Step 5:** Hand the user `https://agent.orbbec.com.cn/sdk/` plus role-specific browser checks. Mark business release accepted only after real build/unit/hardware evidence and the user's final page result. Record pass/fail/not-run counts, exact versions and rollback location.
