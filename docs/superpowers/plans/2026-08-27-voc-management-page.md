# Agent Platform VOC Management Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/admin/voc` to Agent Platform so management viewers, platform administrators, and platform owners can securely browse, filter, paginate, and inspect all VOC records.

**Architecture:** The existing same-origin VOC BFF remains the only browser-to-VOC path. The BFF authorizes Platform management roles, signs a minimal `voc.read_all` downstream token, strictly validates the VOC response, enriches Platform submitter UUIDs through a dedicated control-database directory reader, and returns a frontend-safe model. A separate React API module and read-only page provide filtering and detail UX without importing any legacy VOC workflow.

**Tech Stack:** Python 3.12, FastAPI, httpx, psycopg 3, Pydantic 2, React 19, TypeScript, Vitest, Vite, pytest.

## Global Constraints

- This plan consumes the private VOC API delivered by `docs/superpowers/plans/2026-08-27-voc-management-read-api.md`; record its deployed commit before production rollout.
- Allowed roles are exactly `management_viewer`, `platform_admin`, and `platform_owner`; `member` receives `403` from the BFF.
- Downstream admin calls carry only `voc.read_all`, never the employee mutation capabilities.
- The page is read-only: no assignment, workflow transition, notes, edit, delete, export, or supplement controls.
- Platform is the sole identity and display-name source; VOC does not gain a second account system.
- The submitter directory endpoint returns only internal UUID and display name.
- API responses and errors use `Cache-Control: no-store` and never log VOC body text or tokens.
- Work directly on `master`, preserve the existing untracked user files, and commit after every independently testable task.

---

## File Structure

- Modify `backend/app/voc_extension/identity.py`: allow signing `voc.read_all`.
- Modify `backend/app/voc_extension/client.py`: accept explicit per-request capabilities instead of always sending the self-service set.
- Create `backend/app/voc_extension/directory.py`: batch display-name resolution and submitter filter options from `platform_control.internal_users`.
- Modify `backend/app/voc_extension/routes.py`: authorize management roles, strictly validate/enrich admin responses, and expose list/detail/submitter routes.
- Modify `backend/app/control_plane/authorization.py`: register the two VOC data routes and submitter route as management reads available to all three management roles.
- Modify `backend/app/main.py`: construct the dedicated VOC submitter directory from the control-plane DSN.
- Modify `backend/tests/test_voc_extension_client.py`: minimal capability token tests.
- Modify `backend/tests/test_voc_extension_routes.py`: forwarding, role denial, validation, enrichment, and upstream failure tests.
- Modify `backend/tests/test_voc_extension_acceptance.py`: middleware-level role and downstream-token acceptance.
- Create `backend/tests/test_voc_submitter_directory.py`: batch query and safe failure tests.
- Modify `backend/tests/test_r1_authorization.py`: authorization matrix for member/viewer/admin/owner.
- Create `webui/src/vocAdminApi.ts`: strict frontend contract and cancellable requests.
- Create `webui/src/vocAdminApi.test.ts`: response validation, query serialization, and error tests.
- Create `webui/src/pages/VocManagementPage.tsx`: read-only list, filters, pagination, states, and detail drawer.
- Create `webui/src/pages/VocManagementPage.test.tsx`: interaction and stale-request tests.
- Modify `webui/src/router.ts`: `/admin/voc` route.
- Modify `webui/src/App.tsx`: role gate and page render.
- Modify `webui/src/AppShell.tsx`: management navigation for all three approved roles.
- Modify `webui/src/documentTitle.ts`: VOC management title.
- Modify `webui/src/auth.ts`: accept `/admin/voc` as a safe login return path.
- Modify related router, shell, title, auth, and cloud-mode tests.
- Modify `webui/src/styles.css`: scoped responsive management-page styles.
- Modify `docs/voc-extension-runbook.md`: deploy, probe, and rollback instructions.

### Task 1: Sign minimal management capabilities downstream

**Files:**
- Modify: `backend/app/voc_extension/identity.py`
- Modify: `backend/app/voc_extension/client.py`
- Modify: `backend/tests/test_voc_extension_client.py`
- Modify: `backend/tests/test_voc_extension_acceptance.py`

**Interfaces:**
- Produces: a keyword-only `capabilities: frozenset[str] = SELF_SERVICE_CAPABILITIES` argument on `VocExtensionClient.request`.
- Produces: signer support for `frozenset({"voc.read_all"})`.
- Consumes later: admin BFF routes pass `capabilities=frozenset({"voc.read_all"})`.

- [ ] **Step 1: Write failing signer/client tests**

Add tests that decode the downstream JWT and assert exact capability sets:

```python
response = await client.request(
    "GET",
    "/api/platform/v1/admin/vocs",
    actor_id=USER_ID,
    capabilities=frozenset({"voc.read_all"}),
)
assert response.status_code == 200
assert _payload(seen.headers["Authorization"].removeprefix("Bearer "))["capabilities"] == ["voc.read_all"]
```

Keep a rejection test for `voc.delete`, and assert ordinary workspace calls still default to the exact sorted self-service capabilities `voc.read_self`, `voc.submit`.

- [ ] **Step 2: Run focused backend tests and verify failure**

Run: `.venv/bin/python -m pytest -q backend/tests/test_voc_extension_client.py backend/tests/test_voc_extension_acceptance.py`

Expected: FAIL because `voc.read_all` and the explicit request argument are unsupported.

- [ ] **Step 3: Implement the capability boundary**

Use shared constants:

```python
VOC_CAPABILITIES = frozenset({"voc.submit", "voc.read_self", "voc.read_all"})
SELF_SERVICE_CAPABILITIES = frozenset({"voc.submit", "voc.read_self"})
```

Validate the request capability set is non-empty and a subset of `VOC_CAPABILITIES`, then pass it unchanged to `PlatformVocTokenSigner.issue`. Do not infer management capability from the downstream URL.

- [ ] **Step 4: Run tests and lint**

Run: `.venv/bin/python -m pytest -q backend/tests/test_voc_extension_client.py backend/tests/test_voc_extension_acceptance.py && .venv/bin/ruff check backend/app/voc_extension backend/tests/test_voc_extension_client.py backend/tests/test_voc_extension_acceptance.py`

Expected: PASS and no Ruff errors.

- [ ] **Step 5: Commit minimal capability signing**

```bash
git add backend/app/voc_extension/identity.py backend/app/voc_extension/client.py backend/tests/test_voc_extension_client.py backend/tests/test_voc_extension_acceptance.py
git commit -m "feat: sign VOC management read capability"
```

### Task 2: Add the submitter directory and management BFF routes

**Files:**
- Create: `backend/app/voc_extension/directory.py`
- Modify: `backend/app/voc_extension/routes.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_voc_submitter_directory.py`
- Modify: `backend/tests/test_voc_extension_routes.py`

**Interfaces:**
- Produces: `VocSubmitterDirectory.names_for(ids: frozenset[UUID]) -> dict[UUID, str]`.
- Produces: `VocSubmitterDirectory.list_submitters()` returning an immutable tuple of `SubmitterOption` values.
- Produces: BFF routes `GET /api/v1/extensions/voc/admin/vocs`, `/admin/vocs/{voc_no}`, and `/admin/submitters`.
- Consumes: private VOC admin list/detail payloads and `voc.read_all` client capability.

- [ ] **Step 1: Write failing directory tests**

Use a fake psycopg connection to assert `names_for` performs one parameterized `WHERE internal_user_id = ANY(%s)` query for at most 100 unique UUIDs and returns exact UUID keys. Assert `list_submitters` returns deterministic `(display_name, internal_user_id)` order and only the two public fields. Database errors become `VocDirectoryUnavailable` without leaking DSNs or SQL.

- [ ] **Step 2: Implement the focused directory reader**

```python
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn


class VocDirectoryUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SubmitterOption:
    internal_user_id: UUID
    display_name: str


class VocSubmitterDirectory:
    def __init__(self, database_url: str, *, connect=psycopg.connect) -> None:
        parsed = validate_control_dsn(database_url, purpose="app")
        self.environment = parsed.environment
        self._database_url = database_url
        self._connect = connect

    def _connection(self):
        return self._connect(
            self._database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000",
            row_factory=dict_row,
        )

    def names_for(self, ids: frozenset[UUID]) -> dict[UUID, str]:
        if len(ids) > 100:
            raise ValueError("too many VOC submitters")
        if not ids:
            return {}
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select internal_user_id, display_name "
                    "from platform_control.internal_users "
                    "where internal_user_id = any(%s)",
                    (list(ids),),
                ).fetchall()
            return {row["internal_user_id"]: row["display_name"] for row in rows}
        except psycopg.Error:
            raise VocDirectoryUnavailable("VOC directory unavailable") from None

    def list_submitters(self) -> tuple[SubmitterOption, ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "select internal_user_id, display_name "
                    "from platform_control.internal_users "
                    "order by display_name, internal_user_id"
                ).fetchall()
            return tuple(SubmitterOption(**row) for row in rows)
        except psycopg.Error:
            raise VocDirectoryUnavailable("VOC directory unavailable") from None
```

Read only `platform_control.internal_users.internal_user_id` and `display_name`; include inactive retained users so historical filters still render names. Reject more than 100 IDs before querying.

- [ ] **Step 3: Write failing management route tests**

Extend the route test app so role and directory are injectable. Assert:

```python
member = await client.get("/api/v1/extensions/voc/admin/vocs")
assert member.status_code == 403
assert upstream.calls == []

manager = await manager_client.get(
    "/api/v1/extensions/voc/admin/vocs?query=发热&submitter_internal_user_id=" + str(USER_ID)
)
assert manager.status_code == 200
assert upstream.calls[-1]["capabilities"] == frozenset({"voc.read_all"})
assert manager.json()["items"][0]["submitter_name"] == "苍渊"
```

Test exact pass-through for query, `submitter_internal_user_id`, `legacy_submitter_name`, dates, cursor, and limit; legacy-name fallback; unknown UUID fallback `未知用户 · 11111111`; detail enrichment; submitter options; malformed/extra downstream fields as `502`; directory outage as `503`; and preservation of `401/403/404/503` from VOC.

- [ ] **Step 4: Implement strict BFF models and enrichment**

Add Pydantic models with `extra="forbid"` for downstream admin summary/page/detail. Refactor `_forward` around an internal `_request_json` that returns `(status_code, dict)` after the existing size, status, UTF-8, and nonstandard-JSON checks. Existing routes wrap it unchanged.

Management handlers must call:

```python
def _manager(request: Request) -> AuthContext:
    context = _actor(request)
    if context.role not in {
        Role.MANAGEMENT_VIEWER, Role.PLATFORM_ADMIN, Role.PLATFORM_OWNER,
    }:
        raise HTTPException(403, "forbidden", headers=_NO_STORE)
    return context
```

For each successful page, resolve all non-null submitter UUIDs in one call. Return frontend items with exact fields `voc_no`, `submitter_internal_user_id`, `submitter_name`, `source`, `latest_content`, `revision`, `analysis_status`, `created_at`, `updated_at`; return detail with the same metadata plus `entries`. Construct `VocSubmitterDirectory` in `create_app` from the existing control DSN and store it as `app.state.voc_submitter_directory`.

- [ ] **Step 5: Run route, directory, and construction tests**

Run: `.venv/bin/python -m pytest -q backend/tests/test_voc_submitter_directory.py backend/tests/test_voc_extension_routes.py backend/tests/test_main.py`

Expected: PASS.

- [ ] **Step 6: Commit the BFF data path**

```bash
git add backend/app/voc_extension/directory.py backend/app/voc_extension/routes.py backend/app/main.py backend/tests/test_voc_submitter_directory.py backend/tests/test_voc_extension_routes.py backend/tests/test_main.py
git commit -m "feat: proxy and enrich VOC management reads"
```

### Task 3: Enforce the Platform authorization matrix

**Files:**
- Modify: `backend/app/control_plane/authorization.py`
- Modify: `backend/tests/test_r1_authorization.py`
- Modify: `backend/tests/test_voc_extension_acceptance.py`

**Interfaces:**
- Produces: middleware authorization for all three management GET routes.
- Consumes: role checks in the BFF as defense in depth.

- [ ] **Step 1: Add a failing role matrix**

For each route template below, assert unauthenticated `401`, member `403`, and management viewer/admin/owner allowed:

```python
VOC_MANAGEMENT_ROUTES = {
    ("GET", "/api/v1/extensions/voc/admin/vocs"),
    ("GET", "/api/v1/extensions/voc/admin/vocs/{voc_no}"),
    ("GET", "/api/v1/extensions/voc/admin/submitters"),
}
```

The acceptance test must pass through `IdentitySecurityMiddleware`, verify a manager call reaches the mock VOC service with only `voc.read_all`, and verify a member call never reaches it.

- [ ] **Step 2: Run authorization tests and verify failure**

Run: `.venv/bin/python -m pytest -q backend/tests/test_r1_authorization.py backend/tests/test_voc_extension_acceptance.py`

Expected: FAIL with `route_not_authorized` or `viewer_route_denied`.

- [ ] **Step 3: Register dedicated management-read routes**

Add `_VOC_MANAGEMENT_ROUTES` to `_OWNER_ROUTES`. In `AuthorizationService.decide`, after member denial and before observation-scope handling, allow this set for `MANAGEMENT_VIEWER`, `PLATFORM_ADMIN`, and `PLATFORM_OWNER` with a stable `voc_management` reason. Do not add them to `_AUTHENTICATED_SELF_ROUTES`.

- [ ] **Step 4: Run authorization tests**

Run: `.venv/bin/python -m pytest -q backend/tests/test_r1_authorization.py backend/tests/test_voc_extension_acceptance.py`

Expected: PASS for the exact role matrix.

- [ ] **Step 5: Commit Platform authorization**

```bash
git add backend/app/control_plane/authorization.py backend/tests/test_r1_authorization.py backend/tests/test_voc_extension_acceptance.py
git commit -m "feat: authorize Platform VOC managers"
```

### Task 4: Add a strict, cancellable frontend admin API

**Files:**
- Create: `webui/src/vocAdminApi.ts`
- Create: `webui/src/vocAdminApi.test.ts`

**Interfaces:**
- Produces: `VocAdminApi.list(filters, signal)`, `.detail(vocNo, signal)`, and `.submitters(signal)`.
- Produces: `VocAdminPage { items, next_cursor }`, `VocAdminDetail`, and `VocSubmitterOption`.
- Consumes later: `VocManagementPage`.

- [ ] **Step 1: Write failing API contract tests**

Test exact response keys, ISO timestamp strings, positive safe revisions, the five analysis statuses, the two sources, entry types, nullable UUIDs, and unknown-field rejection. Assert filters serialize with `URLSearchParams` and absent values are omitted:

```typescript
await api.list({ query: "发热", submitterInternalUserId: USER_ID, legacySubmitterName: null, createdFrom: "2026-08-01", createdTo: "2026-09-01", cursor: null, limit: 50 }, signal);
expect(fetchMock).toHaveBeenCalledWith(
  expect.stringContaining("/api/v1/extensions/voc/admin/vocs?"),
  { credentials: "include", signal },
);
```

- [ ] **Step 2: Run the test and verify failure**

Run: `npm --prefix webui test -- vocAdminApi.test.ts`

Expected: FAIL because `vocAdminApi.ts` is absent.

- [ ] **Step 3: Implement the exact frontend contract**

Define:

```typescript
export type VocAnalysisStatus = "pending" | "claimed" | "succeeded" | "failed" | "not_requested";
export interface VocAdminSummary {
  voc_no: string; submitter_internal_user_id: string | null; submitter_name: string;
  source: "platform" | "dingtalk"; latest_content: string; revision: number;
  analysis_status: VocAnalysisStatus; created_at: string; updated_at: string;
}
export interface VocAdminPage { items: VocAdminSummary[]; next_cursor: string | null }
```

Use exact-object parsers like `vocApi.ts`, `encodeURIComponent` for detail paths, `credentials: "include"`, the provided `AbortSignal`, and `VocAdminApiError(status, code)` for non-2xx JSON responses.

- [ ] **Step 4: Run tests and TypeScript build**

Run: `npm --prefix webui test -- vocAdminApi.test.ts && npm --prefix webui run build`

Expected: test and build pass.

- [ ] **Step 5: Commit the frontend API**

```bash
git add webui/src/vocAdminApi.ts webui/src/vocAdminApi.test.ts
git commit -m "feat: add VOC management browser API"
```

### Task 5: Wire the route, role-aware navigation, and page shell

**Files:**
- Modify: `webui/src/router.ts`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/AppShell.tsx`
- Modify: `webui/src/documentTitle.ts`
- Modify: `webui/src/auth.ts`
- Modify: `webui/src/router.test.ts`
- Modify: `webui/src/router.brain.test.ts`
- Modify: `webui/src/AppShell.brain.test.tsx`
- Modify: `webui/src/cloudMode.test.tsx`
- Modify: `webui/src/documentTitle.test.tsx`
- Modify: `webui/src/auth.test.ts`

**Interfaces:**
- Produces: route `{ name: "admin-voc" }` at `/admin/voc`.
- Produces: visible `VOC 管理` navigation for all three approved management roles.
- Consumes later: `VocManagementPage` render case.

- [ ] **Step 1: Write failing route and navigation tests**

Assert `parseRoute("/admin/voc")`, `routePath`, `routeSection`, document title, and safe login return path. Render `AppShell` for each role and assert:

- member: no management entry;
- management viewer: top-level management entry links directly to `/admin/voc`, admin nav contains only `VOC 管理`;
- platform admin/owner: full existing admin nav plus `VOC 管理`.

Render `App` at `/admin/voc` with account payloads and assert member receives the permission page while all three management roles reach the VOC page placeholder.

- [ ] **Step 2: Run focused UI tests and verify failure**

Run: `npm --prefix webui test -- router.test.ts router.brain.test.ts AppShell.brain.test.tsx cloudMode.test.tsx documentTitle.test.tsx auth.test.ts`

Expected: FAIL because `admin-voc` is unknown.

- [ ] **Step 3: Implement route and role navigation**

Add `admin-voc` to `Route`, parse/path/title logic, and the `/admin/voc` login return allowlist. Add `{ label: "VOC 管理", path: "/admin/voc", section: "admin" }` to admin navigation. For a management viewer, `navigationFor` must add one top-level management entry pointing to `/admin/voc`, and `managementNavigation` must contain only the VOC item. Add `admin-voc` to `viewerRouteAllowed` and to `productPage`.

- [ ] **Step 4: Run focused UI tests**

Run: `npm --prefix webui test -- router.test.ts router.brain.test.ts AppShell.brain.test.tsx cloudMode.test.tsx documentTitle.test.tsx auth.test.ts`

Expected: PASS with no regressions to existing admin navigation.

- [ ] **Step 5: Commit route and navigation**

```bash
git add webui/src/router.ts webui/src/App.tsx webui/src/AppShell.tsx webui/src/documentTitle.ts webui/src/auth.ts webui/src/router.test.ts webui/src/router.brain.test.ts webui/src/AppShell.brain.test.tsx webui/src/cloudMode.test.tsx webui/src/documentTitle.test.tsx webui/src/auth.test.ts
git commit -m "feat: add VOC management route"
```

### Task 6: Build the read-only management page

**Files:**
- Create: `webui/src/pages/VocManagementPage.tsx`
- Create: `webui/src/pages/VocManagementPage.test.tsx`
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Produces: `VocManagementPage({ api = vocAdminApi })`.
- Consumes: list/detail/submitter APIs from Task 4.

- [ ] **Step 1: Write failing page interaction tests**

Create a fake API and test:

- initial skeleton, then two rows with submitter/source/time/status labels;
- true empty state versus filter-no-results state;
- keyword, Platform submitter, legacy DingTalk submitter name, start date, and end date submission resets the cursor;
- “加载更多” appends and does not replace rows;
- load-more failure preserves existing rows and shows retry;
- row click opens complete ordered entries and a close button;
- no edit/delete/assign/supplement controls exist;
- resolving an older request after a newer filter request does not overwrite the new results;
- unmount aborts outstanding list/detail/submitter calls.

- [ ] **Step 2: Run the page test and verify failure**

Run: `npm --prefix webui test -- pages/VocManagementPage.test.tsx`

Expected: FAIL because the page does not exist.

- [ ] **Step 3: Implement list/filter state with request cancellation**

Use one `AbortController` per list request plus an incrementing request ID. Keep `items`, `nextCursor`, `loading`, `loadingMore`, `error`, and `hasAppliedFilters` separate. Convert date inputs to ISO bounds: local start-of-day for `created_from`, and the day after the selected end date for exclusive `created_to`.

Render a semantic form with labels `关键词`, `平台提交人`, `历史钉钉提交人`, `开始日期`, `结束日期`, buttons `查询` and `清空`; a table/list with columns `VOC 编号`, `提交人`, `内容摘要`, `来源`, `提交时间`, `分析状态`; and `加载更多` only when `next_cursor` is non-null. The historical-name input maps to `legacy_submitter_name`; selecting a Platform user clears it, and typing a historical name clears the Platform selector so one request never combines mutually exclusive submitter identities.

- [ ] **Step 4: Implement read-only detail and resilient states**

Open a right-side `<aside aria-label="VOC 详情">` with metadata and ordered entry cards. Map statuses exactly:

```typescript
const ANALYSIS_LABEL = {
  pending: "待分析", claimed: "分析中", succeeded: "已分析",
  failed: "分析失败", not_requested: "未请求分析",
} as const;
```

Show skeletons during first load, retain filters on error, use `重新加载`, preserve loaded items on pagination failure, and never render mutation controls.

- [ ] **Step 5: Add scoped responsive styles and run UI verification**

Prefix all new selectors with `.voc-management`. At desktop use a compact filter bar and readable list/table; below 760px stack filters, turn rows into cards, and keep the detail drawer within `94vw`. Reuse existing CSS variables and visible focus styles.

Run: `npm --prefix webui test -- pages/VocManagementPage.test.tsx && npm --prefix webui run build`

Expected: tests and production build pass.

- [ ] **Step 6: Commit the management page**

```bash
git add webui/src/pages/VocManagementPage.tsx webui/src/pages/VocManagementPage.test.tsx webui/src/App.tsx webui/src/styles.css
git commit -m "feat: build read-only VOC management page"
```

### Task 7: Verify end to end and document production rollout

**Files:**
- Modify: `docs/voc-extension-runbook.md`
- Modify: `backend/tests/test_voc_extension_acceptance.py`

**Interfaces:**
- Produces: a repeatable rollout from the recorded VOC SHA to the Platform SHA.
- Produces: content-free health, authorization, list count, filtering, and detail probes.

- [ ] **Step 1: Add the final acceptance scenario**

In the Platform acceptance test, return a realistic private admin page, resolve two UUIDs to different names, verify the browser response contains both names, verify a detail request, and assert a member gets `403` without downstream traffic. Assert every response has `Cache-Control: no-store`.

- [ ] **Step 2: Run backend and frontend verification**

Run:

```bash
.venv/bin/python -m pytest -q backend/tests/test_voc_extension_client.py backend/tests/test_voc_extension_routes.py backend/tests/test_voc_submitter_directory.py backend/tests/test_voc_extension_acceptance.py backend/tests/test_r1_authorization.py backend/tests/test_main.py
.venv/bin/ruff check backend/app backend/tests
npm --prefix webui test
npm --prefix webui run build
.venv/bin/python -m pytest -q
```

Expected: focused backend tests, Ruff, all Vitest tests, frontend build, and the full backend suite pass.

- [ ] **Step 3: Update the runbook**

Document this order: deploy the recorded VOC commit and migration first; verify private health and management API as a signed Platform caller; deploy Platform; verify `/admin/voc` as manager and as member; then expose navigation. Probes may print HTTP status, item count, and duration only. Rollback hides Platform navigation/BFF first and then rolls back the VOC container; no database rows are deleted.

- [ ] **Step 4: Commit verification and operations**

```bash
git add backend/tests/test_voc_extension_acceptance.py docs/voc-extension-runbook.md
git commit -m "docs: operationalize VOC management page"
```

## Platform Completion Gate

- `git status --short` shows only the user's pre-existing untracked files: `.claude/`, `docs/2026-06-29-platform-flywheel-review-design.md`, `registry.local.yaml`, `webui/public/ai-admin-logo.svg`, and `webui/public/ai-fae-logo.svg`.
- `management_viewer`, `platform_admin`, and `platform_owner` can reach `/admin/voc`; `member` is denied by middleware and route code.
- Browser calls never contact the VOC service directly.
- Admin downstream JWTs contain exactly `voc.read_all`.
- Submitter names are batch-resolved without N+1 queries.
- The page contains no mutation controls and existing employee VOC tests still pass.
- Record both repository SHAs and production smoke-test results before declaring deployment complete.
