# SDK 简洁工作区与自动开通 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让合格的钉钉员工首次点击 SDK 工作区即可自动开通个人禅道账号，并从简洁的四个入口进入实际业务页面。

**Architecture:** 平台目录和显式授权决定准入。SDK SSO 在持久化锁下核对确定性账号、调用私网禅道建号接口、验证账号和产品权限，然后签发 45 秒单次票据。票据只保存固定目标枚举，消费时映射成禅道站内路径。完整禅道保持独立浏览器源。

**Tech Stack:** PostgreSQL 17 migration, Python 3.11/FastAPI/psycopg/httpx/pytest, ZenTao 22.6 API v1/v2, React/TypeScript/Vitest, Docker Compose.

## Global Constraints

- 生产服务器禁止使用 `nginx -t -c` 或 `nginx -T -c`；仅完整 `nginx -t` 并核实运行用户和临时目录。
- 不用平台服务账号替员工写需求、任务或测试；禅道里的写入归属个人会话。
- 新账号名 `sdk_` 加内部 UUID 的 26 位小写 Base32 无填充编码，整串 30 字符；不按姓名或邮箱认领旧账号。
- 目录硬过期、人员停用和未配置部门策略均不得扩大准入；现有人工授权继续保留。
- 建号响应不确定时须核对确定账号，不盲目再次创建。
- 禅道完整页面使用独立浏览器源；DNS 未配置时不切换 `sdk.orbbec.com.cn`。

---

### Task 1: 核实禅道 22.6 的建号与权限合同

**Files:**
- Create: `docs/reviews/2026-10-20-sdk-zen-provision-contract.md`

**Interfaces:**
- Consumes: ZenTao 22.6 image `hub.zentao.net/app/zentao:22.6`, existing product/execution and SSO entry.
- Produces: `POST /api.php/v1/users`, `GET /api.php/v1/users/{account}` 的请求、响应及权限组/产品可见性结论。

- [ ] **Step 1: Record expected source contract.** Confirm v1 `users.php::post()` accepts `account,password,realname,group,visions`, returns 201, and reads group from comma-separated string. Record relevant source lines and exact response fields in the review.
- [ ] **Step 2: Run isolated 22.6 instance.** Use a local temporary database and container with no production data. Create a sample user via private v1 API; verify `zt_user`, `zt_usergroup`, product visibility, `apilogin`, and loss of access after group removal. Record commands and sanitized responses in review.
- [ ] **Step 3: Map fixed ZenTao route destinations.** Confirm exact requirement/task/test/release URLs with authenticated 22.6 session and no open redirect; record them.
- [ ] **Step 4: Commit the contract.** `git add docs/reviews/2026-10-20-sdk-zen-provision-contract.md && git commit -m 'docs(sdk): verify ZenTao provisioning contract'`.

### Task 2: Directory eligibility and durable provisioning state

**Files:**
- Create: `backend/control_migrations/120_sdk_auto_membership.sql`
- Modify: `backend/app/sdk_workbench/sso_repository.py`
- Test: `backend/tests/test_sdk_sso_repository.py`

**Interfaces:**
- Consumes: `AuthContext.internal_user_id`, active directory generation and department closure, `sdk.members`, `sdk.sso_bindings`.
- Produces: `SdkSsoRepository.provision_slot(user_id)` yielding current verified display name and binding state; `SdkSsoRepository.complete_provision(user_id, account)` and fail-closed ticket SQL.

- [ ] **Step 1: Write PostgreSQL tests** for SDK/allowed collaborator descendants, empty allowlist, manual member, inactive or hard-stale identity, parallel lock, and consume-after-revocation. Run `.venv/bin/python -m pytest backend/tests/test_sdk_sso_repository.py -q`; expect new tests to fail.
- [ ] **Step 2: Add migration** with `sdk.allowed_departments`, provisioning status, a source marker on auto membership, a server-side `sdk.current_sso_identity(uuid)` eligibility function, and ticket issue/consume checks. Grant only required functions to the app role; never grant raw directory-table SELECT.
- [ ] **Step 3: Implement repository** using one advisory lock per UUID through read/verify/write, deterministic name `sdk_` + `base64.b32encode(user_id.bytes).decode().rstrip('=').lower()`, and explicit uncertain-state reconciliation. Run the same tests; expect pass.
- [ ] **Step 4: Commit** migration, repository, tests with `git commit -m 'feat(sdk): enforce directory scoped SSO provisioning state'`.

### Task 3: Private ZenTao account provisioner

**Files:**
- Create: `backend/app/sdk_workbench/provision.py`
- Modify: `backend/app/config.py`, `backend/app/main.py`, `backend/app/sdk_workbench/sso.py`
- Test: `backend/tests/test_sdk_provision.py`, `backend/tests/test_sdk_sso_routes.py`

**Interfaces:**
- Consumes: `provision_slot`, dedicated secret-file credential, private ZenTao origin and configured SDK group/product IDs.
- Produces: `ZenTaoProvisioner.ensure(account, display_name)` verifying account and rights; `SdkSsoService.start` issues tickets only after binding is confirmed.

- [ ] **Step 1: Write failing mocked HTTP tests** for first create, existing confirmed binding, account conflict, uncertain timeout, retry after reconciliation, wrong group, and unavailable ZenTao. Run `.venv/bin/python -m pytest backend/tests/test_sdk_provision.py backend/tests/test_sdk_sso_routes.py -q`; expect failures.
- [ ] **Step 2: Implement bounded private API client.** Use a dedicated ZenTao user with user-create scope, random undisclosed password, v1 user creation and exact account/group verification; reject ambiguous envelopes. Add validated secret file and group config; disabled by default until verified.
- [ ] **Step 3: Wire SSO first entry** under the repository lock, preserving existing `cangyuan` binding. Run tests; expect pass.
- [ ] **Step 4: Commit** with `git commit -m 'feat(sdk): automatically provision verified ZenTao users'`.

### Task 4: Fixed destinations and simple SDK entry

**Files:**
- Modify: `backend/control_migrations/120_sdk_auto_membership.sql`, `backend/app/sdk_workbench/sso.py`, `webui/src/sdk/SdkWorkbench.tsx`, `webui/src/sdk/sdkApi.ts`, `webui/src/sdk/sdkWorkbench.css`
- Test: `backend/tests/test_sdk_sso_routes.py`, `webui/src/sdk/SdkWorkbench.test.tsx`

**Interfaces:**
- Consumes: `target` in `requirements|tasks|tests|releases`, one-use ticket.
- Produces: 303 to an exact server-owned ZenTao relative path; four actionable, accessible SDK cards.

- [ ] **Step 1: Write failing tests** proving each target starts SSO, invalid target fails, and ticket cannot be replayed or redirected. Run backend and Vitest targeted suites; expect failures.
- [ ] **Step 2: Store target with ticket** and map only fixed Zen routes from Task 1. Keep no arbitrary return URL in browser input.
- [ ] **Step 3: Replace informational steps with four action cards** in `/sdk`; show precise loading, access, provisioning and retry states. Remove hardcoded IP fallback from SSO-enabled mode.
- [ ] **Step 4: Run targeted tests and `npm run build`; commit** with `git commit -m 'feat(sdk): add direct requirement task test and release entry'`.

### Task 5: Release and production scenario proof

**Files:**
- Create: `docs/reviews/2026-10-20-sdk-simple-workspace-release.md`

**Interfaces:**
- Consumes: reviewed merged commit, migration checksum, sanitized production config and backup.
- Produces: deployed `/sdk` and backend, rollback location, actual scenario results.

- [ ] **Step 1: Run full relevant backend and web tests**, migration on isolated PostgreSQL and ZenTao 22.6 instance; inspect diff and request a code review.
- [ ] **Step 2: Merge reviewed branch to clean main** and verify release image exact commit. Back up production ZenTao database/config and platform release pointer before applying migration or config.
- [ ] **Step 3: Deploy with feature gate off**, migrate, configure verified SDK group and private provision credential, then enable. Check existing binding, new first login, replay, inactive, scope removal, and per-person write attribution.
- [ ] **Step 4: Verify production dependencies**: complete `nginx -t` only with safe user/temp state; SDK, platform login, `/office/`, administrative service and full static asset hash. Record exact results, unresolved browser acceptance and rollback path; commit review document.
